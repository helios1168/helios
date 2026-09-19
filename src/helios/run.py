"""The ``helios run`` pipeline (SPEC §7.1) with report capture (SPEC §6.2).

Library code lives here; argument parsing lives in ``commands/run.py``.
Several beads run in parallel up to ``max_parallel`` (default 3).
``--tmux`` and ``--in-window`` belong to a later bead and are refused here.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any, cast

from helios import attempt as attempt_mod
from helios import beads as beads_mod
from helios import config as config_mod
from helios import envelope as envelope_mod
from helios import events as events_mod
from helios import jsonio
from helios import memory as memory_mod
from helios import ownership as ownership_mod
from helios import prompt as prompt_mod
from helios import stageset
from helios import telemetry as telemetry_mod
from helios import worktree as worktree_mod
from helios.harness.base import Harness, LaunchSpec

class MemoryLookupError(RuntimeError):
    """A memory backend lookup failed, at preflight or while assembling the
    prompt (SPEC §7.1, §13). Either way there is no "" fallback: the caller
    prints this message and refuses (round-1-fix item 2).
    """


class VerifyStartError(RuntimeError):
    """A fresh verify worktree could not resolve its parent's ``output_commit``
    (SPEC §7.3). Preflight (``_check_verify_worktree_start``) already refuses
    this in every production path; raising here instead of falling back to
    ``main`` means a caller that bypasses preflight never gets a silently
    wrong base commit (round-1-fix item 2).
    """


_RUNNING: dict[str, subprocess.Popen[str]] = {}
_RUNNING_LOCK = threading.Lock()


class _InterruptFlag(threading.Event):
    """``threading.Event`` whose ``clear()`` also resets the SIGINT reentry guard.

    ``_handle_sigint`` sets ``_SIGINT_SETTING`` before calling ``set()`` on this
    flag, so a nested SIGINT delivered while ``set()`` is still acquiring its
    condition lock returns immediately instead of re-entering ``set()`` and
    deadlocking on the non-reentrant lock (SPEC §7.1, round 7). The guard must
    come back down wherever this flag is cleared, whoever clears it, so a
    later, genuine SIGINT still fires; overriding ``clear()`` here does that
    regardless of the caller.
    """

    def clear(self) -> None:
        global _SIGINT_SETTING
        _SIGINT_SETTING = False
        super().clear()


_INTERRUPT = _InterruptFlag()
_SIGINT_SETTING = False
_RUN_ACTIVE = threading.Event()
_SEQ_THREADS: list[threading.Thread] = []
_SEQ_DONE: set[int] = set()
_SEQ_LOCK = threading.Lock()


def _reg_add(key: str, proc: subprocess.Popen[str]) -> None:
    """Register a running child; always through ``_RUNNING_LOCK``."""
    with _RUNNING_LOCK:
        _RUNNING[key] = proc
    if _INTERRUPT.is_set():
        _start_sequence(proc)


def _reg_remove(key: str) -> None:
    """Drop a finished child from the registry."""
    with _RUNNING_LOCK:
        _RUNNING.pop(key, None)


def _reg_snapshot() -> list[subprocess.Popen[str]]:
    """The currently running children."""
    with _RUNNING_LOCK:
        return list(_RUNNING.values())


def _start_sequence(proc: subprocess.Popen[str]) -> threading.Thread | None:
    """Start one stop-sequence thread for a group, once per pgid (SPEC §7.1)."""
    with _SEQ_LOCK:
        if proc.pid in _SEQ_DONE:
            return None
        _SEQ_DONE.add(proc.pid)
        thread = threading.Thread(
            target=_stop_sequence, args=(proc, proc.pid),
            name="stopper-seq", daemon=True,
        )
        _SEQ_THREADS.append(thread)
    thread.start()
    return thread


def _seq_threads() -> list[threading.Thread]:
    """A snapshot of the stop-sequence threads so far."""
    with _SEQ_LOCK:
        return list(_SEQ_THREADS)
_RUN_DEPTH = 0
_RUN_DEPTH_LOCK = threading.Lock()
_PREV_SIGINT: Callable[[int, FrameType | None], object] | int | None = None


def _handle_sigint(signum: int, frame: FrameType | None) -> None:
    """Record a SIGINT and return immediately (SPEC §7.1 Interrupts).

    The handler never blocks and takes no lock of its own. A module-level
    plain bool, ``_SIGINT_SETTING``, is set before calling ``Event.set()``
    and tested first: a SIGINT delivered while that call is still acquiring
    the Event's internal lock (a nested call on this same thread, since
    Python signal handlers only run on the main thread between bytecodes)
    sees the guard already up and returns without touching ``set()`` again,
    so it never blocks on the non-reentrant lock ``set()`` is holding one
    frame up (SPEC §7.1, round 7 reentry). ``_InterruptFlag.clear()`` brings
    the guard back down, so later, genuine SIGINTs still fire.
    """
    global _SIGINT_SETTING
    if _SIGINT_SETTING:
        return
    _SIGINT_SETTING = True
    _INTERRUPT.set()


def _stopper_main() -> None:
    """Start one stop-sequence thread per running group (SPEC §7.1).

    Blocks in ``Event.wait()`` with no timeout and wakes once when the
    flag is set. Every group registered at that point gets its own
    stop-sequence thread at once; groups that register later get theirs
    immediately at registration (see ``_reg_add``). The thread then
    returns; ``run_many`` joins it and every sequence thread.
    """
    _INTERRUPT.wait()
    if not _RUN_ACTIVE.is_set():
        return
    for proc in _reg_snapshot():
        _start_sequence(proc)


def _run_depth_enter() -> int:
    """Count one more active run layer; return the new depth."""
    global _RUN_DEPTH
    with _RUN_DEPTH_LOCK:
        _RUN_DEPTH += 1
        return _RUN_DEPTH


def _run_depth_exit() -> None:
    """Leave one run layer."""
    global _RUN_DEPTH
    with _RUN_DEPTH_LOCK:
        _RUN_DEPTH -= 1


def _install_handler() -> bool:
    """Install the SIGINT handler for this run; True when installed."""
    global _PREV_SIGINT
    if threading.current_thread() is not threading.main_thread():
        return False
    try:
        _PREV_SIGINT = signal.signal(signal.SIGINT, _handle_sigint)
    except ValueError:
        return False
    return True


def _restore_handler(*, keep_if_interrupted: bool = False) -> None:
    """Restore the previous SIGINT handler.

    With ``keep_if_interrupted``, a run this process actually interrupted
    leaves ``_handle_sigint`` installed instead of restoring ``prev``: SPEC
    §7.1 "later SIGINTs are ignored" covers signals arriving after this
    call decides to restore too, since a trailing signal from the same
    storm can otherwise race the process's own exit and kill it by the
    signal's raw disposition instead of the intended exit code (round-1-fix
    item 7, ``run_one_in_window``). The bookkeeping (``_PREV_SIGINT``)
    still clears either way, so a later, uninterrupted run in the same
    process restores normally.
    """
    global _PREV_SIGINT
    prev, _PREV_SIGINT = _PREV_SIGINT, None
    if prev is None:
        return
    if keep_if_interrupted and _SIGINT_SETTING:
        return
    try:
        signal.signal(signal.SIGINT, prev)  # type: ignore[arg-type]
    except ValueError:
        pass


@contextlib.contextmanager
def interrupt_scope():
    """Install SPEC §7.1 Interrupts for the caller's whole run.

    Shared by ``run_many``, ``run_one_in_window`` (which layers SIGHUP and
    SIGTERM on top) and ``helios resume`` (SPEC §9.2, round-1-fix item 6):
    the first SIGINT anywhere in scope sets the run-wide flag, a stopper
    thread runs the stop sequence on every currently running group, and a
    nested scope (an inner ``run_one`` call) only counts depth, never
    reinstalling the handler or clearing the flag it did not set.
    """
    depth = _run_depth_enter()
    outermost = depth == 1
    stopper: threading.Thread | None = None
    try:
        if outermost:
            _INTERRUPT.clear()
            with _SEQ_LOCK:
                _SEQ_THREADS.clear()
                _SEQ_DONE.clear()
            if _install_handler():
                _RUN_ACTIVE.set()
                stopper = threading.Thread(
                    target=_stopper_main, name="_stopper_main", daemon=True
                )
                stopper.start()
        yield
    finally:
        try:
            if outermost:
                _RUN_ACTIVE.clear()
                if stopper is not None:
                    # See run_many: never call set() on the main thread while
                    # our handler is installed.
                    waker = threading.Thread(
                        target=_INTERRUPT.set, name="_interrupt_waker", daemon=True
                    )
                    waker.start()
                    waker.join()
                    stopper.join()
                for thread in _seq_threads():
                    thread.join()
        finally:
            if outermost:
                _restore_handler()
            _run_depth_exit()


def sha256_text(text: str) -> str:
    """sha256 hex of one input (SPEC §8.3)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_input_hashes(
    *,
    prompt: str,
    bead_json: str,
    docs: list[tuple[str, str]],
    memories: dict[str, str],
    unit_text: str | None,
) -> dict[str, str]:
    """Hash the prompt, bead JSON, each doc and memory value, unit file."""
    hashes: dict[str, str] = {
        "prompt": sha256_text(prompt),
        "bead": sha256_text(bead_json),
    }
    for entry, text in docs:
        hashes[f"docs/{entry}"] = sha256_text(text)
    for key, value in memories.items():
        hashes[f"memory/{key}"] = sha256_text(value)
    if unit_text is not None:
        hashes["unit"] = sha256_text(unit_text)
    return hashes


def _write_input_json(path: Path, payload: dict[str, object]) -> None:
    """Write ``input.json``: sorted, indented, to a temp file then ``os.replace`` (SPEC §7.1 step 6)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="input.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(jsonio.dumps(payload, indent=2, sort_keys=True))
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _memory_backend(beads: beads_mod.BeadsLike, config: config_mod.Config) -> memory_mod.Backend:
    """The memory backend for prompt assembly, over the run's own injected beads (SPEC §13).

    Unlike ``helios.memory.open_backend``, which always talks to a real
    ``bd``, this reuses whichever ``beads`` object the run was given (a real
    ``Beads`` or a test's ``FakeBeads``), so memory injection is testable the
    same way bead metadata already is (see ``memory_has_for``). Both concrete
    types satisfy ``BeadsBackend``'s narrower ``remember``/``recall``/
    ``memories`` interface even though the general ``BeadsLike`` protocol
    used across ``run.py`` does not name it.
    """
    if config.memory.backend == "files":
        return memory_mod.FilesBackend(config.hub / config.memory.export_dir)
    if config.memory.backend == "beads":
        return memory_mod.BeadsBackend(
            cast(Any, beads), config.hub / config.memory.export_dir
        )
    raise ValueError(f"unknown memory backend '{config.memory.backend}'")


def classify_execution(
    *,
    interrupted: bool,
    timed_out: bool,
    launch_failed: bool,
    exit_code: int | None,
    native_error: str | None,
    has_candidate: bool,
    report_valid: bool,
) -> envelope_mod.ExecutionStatus:
    """Map process facts and capture outcome to SPEC §4.4, in SPEC order."""
    if launch_failed:
        return envelope_mod.ExecutionStatus.LAUNCH_FAILED
    if interrupted:
        return envelope_mod.ExecutionStatus.INTERRUPTED
    if timed_out:
        return envelope_mod.ExecutionStatus.TIMED_OUT
    if report_valid:
        return envelope_mod.ExecutionStatus.COMPLETED
    if has_candidate:
        return envelope_mod.ExecutionStatus.INVALID_OUTPUT
    if exit_code is not None and exit_code != 0:
        return envelope_mod.ExecutionStatus.CRASHED
    if native_error is not None:
        return envelope_mod.ExecutionStatus.CRASHED
    return envelope_mod.ExecutionStatus.MISSING_OUTPUT


def _group_gone(pgid: int) -> bool:
    """True only when the whole child process group is gone (SPEC §7.1)."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False
    return False


def _wait_group_gone(proc: subprocess.Popen[str], pgid: int, timeout_s: float) -> bool:
    """Wait until the group is gone or the limit hits; reap the leader."""
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        proc.poll()
        if _group_gone(pgid):
            proc.poll()
            return True
        time.sleep(0.05)
    proc.poll()
    return _group_gone(pgid)


def _signal_group(proc: subprocess.Popen[str], pgid: int, sig: int) -> None:
    """Send a signal to the child's process group, else the child (SPEC §7.1)."""
    try:
        os.killpg(pgid, sig)
        return
    except (ProcessLookupError, PermissionError, ValueError, OSError):
        pass
    try:
        proc.send_signal(sig)
    except (ProcessLookupError, ValueError, OSError):
        pass


def _stop_sequence(proc: subprocess.Popen[str], pgid: int) -> int | None:
    """SIGINT, wait up to 10 s, SIGTERM, up to 5 s, SIGKILL (SPEC §7.1)."""
    _signal_group(proc, pgid, signal.SIGINT)
    if _wait_group_gone(proc, pgid, 10):
        return proc.poll()
    _signal_group(proc, pgid, signal.SIGTERM)
    if _wait_group_gone(proc, pgid, 5):
        return proc.poll()
    _signal_group(proc, pgid, signal.SIGKILL)
    _wait_group_gone(proc, pgid, 5)
    return proc.poll()


def _tee_pump(pipe: object, out_fh: object) -> None:
    """Copy child stdout bytes to the attempt log and this process's stdout.

    ``--in-window`` mirrors the child's stdout bytes to its own stdout as
    well as ``stdout.jsonl`` (SPEC §9.1).
    """
    try:
        while True:
            # read1, not read: a plain read(n) on a BufferedReader can block
            # collecting up to n bytes instead of returning what is already
            # there, so a short write followed by a long silence would never
            # reach this process's own stdout until the child exits.
            chunk = pipe.read1(4096)  # type: ignore[attr-defined]
            if not chunk:
                break
            out_fh.write(chunk)  # type: ignore[attr-defined]
            out_fh.flush()  # type: ignore[attr-defined]
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
    except (OSError, ValueError):
        pass
    finally:
        try:
            pipe.close()  # type: ignore[attr-defined]
        except OSError:
            pass


def _launch_and_wait(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdin_text: str | None,
    stdout_path: Path,
    stderr_path: Path,
    timeout_s: int,
    run_key: str,
    on_launched: Callable[[int], None] | None = None,
    stop_path: Path | None = None,
    tee_stdout: bool = False,
) -> tuple[int | None, bool, bool, bool, int | None]:
    """Run one attempt; return (exit, timed_out, interrupted, launch_failed, pid).

    The child runs in its own session and every signal goes to its process
    group (SPEC §7.1 step 7). ``launched`` with the child pid is recorded
    through ``on_launched`` as soon as ``Popen`` returns, before waiting
    (SPEC §8.2). A set interrupt flag skips the launch and records
    ``interrupted`` without starting a child. While waiting, ``stop_path``
    (when given) is checked for existence about once a second; its
    appearance runs the stop sequence early (SPEC §7.1 step 7, §9.2). The
    caller does the SPEC §4.4 point 2 classification test of that same file,
    once, after this returns and before parse. ``tee_stdout`` additionally
    copies the child's stdout bytes to this process's own stdout (SPEC §9.1).
    """
    if _INTERRUPT.is_set():
        return None, False, True, False, None
    try:
        out_fh = open(stdout_path, "wb" if tee_stdout else "w")
    except OSError:
        return None, False, False, True, None
    try:
        err_fh = open(stderr_path, "w")
    except OSError:
        out_fh.close()
        return None, False, False, True, None
    with out_fh, err_fh:
        try:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                stdout=subprocess.PIPE if tee_stdout else out_fh,
                stderr=err_fh,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                env=env,
                text=not tee_stdout,
                start_new_session=True,
            )
        except OSError:
            return None, False, False, True, None
        child_pid: int | None = proc.pid
        pgid = child_pid
        tee_thread: threading.Thread | None = None
        if tee_stdout and proc.stdout is not None:
            tee_thread = threading.Thread(
                target=_tee_pump, args=(proc.stdout, out_fh), name="tee-stdout", daemon=True
            )
            tee_thread.start()
        if stdin_text is not None and proc.stdin is not None:
            try:
                data = stdin_text.encode() if tee_stdout else stdin_text
                proc.stdin.write(data)  # type: ignore[arg-type]
                proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass
        if on_launched is not None:
            on_launched(child_pid)
        _reg_add(run_key, proc)

        def _done(
            exit_code: int | None, *, timed_out: bool, interrupted: bool
        ) -> tuple[int | None, bool, bool, bool, int | None]:
            if tee_thread is not None:
                tee_thread.join(timeout=5)
            return exit_code, timed_out, interrupted, False, child_pid

        try:
            start = time.monotonic()
            last_stop_check = start
            while True:
                if _INTERRUPT.is_set():
                    exit_code = proc.wait()
                    _wait_group_gone(proc, pgid, 30)
                    return _done(exit_code, timed_out=False, interrupted=True)
                if stop_path is not None and time.monotonic() - last_stop_check >= 1.0:
                    last_stop_check = time.monotonic()
                    if stop_path.exists():
                        exit_code = _stop_sequence(proc, pgid)
                        return _done(exit_code, timed_out=False, interrupted=False)
                try:
                    exit_code = proc.wait(timeout=0.05)
                except subprocess.TimeoutExpired:
                    pass
                else:
                    if _INTERRUPT.is_set():
                        _wait_group_gone(proc, pgid, 30)
                        return _done(exit_code, timed_out=False, interrupted=True)
                    return _done(exit_code, timed_out=False, interrupted=False)
                if time.monotonic() - start >= timeout_s:
                    exit_code = _stop_sequence(proc, pgid)
                    return _done(exit_code, timed_out=True, interrupted=False)
        except KeyboardInterrupt:
            exit_code = _stop_sequence(proc, pgid)
            return _done(exit_code, timed_out=False, interrupted=True)
        finally:
            _reg_remove(run_key)


def _run_shell(
    command: str, cwd: Path, log_path: Path, run_key: str
) -> tuple[bool, int | None, str]:
    """Run a configured shell command; the group stops on interrupt (SPEC §7.1).

    A check stopped by the interrupt fails with detail ``interrupted``.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if _INTERRUPT.is_set():
        log_path.write_text(f"$ {command}\ninterrupted before launch\n")
        return False, None, "interrupted"
    try:
        proc = subprocess.Popen(
            ["bash", "-c", command],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        log_path.write_text(f"failed to start: {exc}\n")
        return False, 127, f"failed to start: {exc}"
    _reg_add(run_key, proc)
    try:
        while True:
            if _INTERRUPT.is_set():
                proc.wait()
                _wait_group_gone(proc, proc.pid, 30)
                try:
                    out, err = proc.communicate(timeout=5)
                except (subprocess.TimeoutExpired, ValueError, OSError):
                    out, err = "", ""
                log_path.write_text(f"$ {command}\ninterrupted\n{out}{err}")
                return False, proc.poll(), "interrupted"
            try:
                out, err = proc.communicate(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                continue
        if _INTERRUPT.is_set():
            try:
                out, err = proc.communicate(timeout=5)
            except (subprocess.TimeoutExpired, ValueError, OSError):
                out, err = "", ""
            log_path.write_text(f"$ {command}\ninterrupted\n{out}{err}")
            return False, proc.poll(), "interrupted"
    finally:
        _reg_remove(run_key)
    code = proc.returncode
    log_path.write_text(f"$ {command}\n(exit {code})\n{out}{err}")
    detail = (out + err).strip()[-2000:]
    return code == 0, code, detail


def _git(worktree: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=worktree, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _git_nul(worktree: Path, *args: str) -> list[str]:
    proc = subprocess.run(
        ["git", *args], cwd=worktree, capture_output=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed")
    return [p for p in proc.stdout.decode("utf-8").split("\0") if p]


def _head_commit(worktree: Path) -> str:
    return _git(worktree, "rev-parse", "HEAD")


def _literal(path: str) -> str:
    """A literal git pathspec, so glob characters never expand (SPEC §7.1)."""
    return f":(literal){path}"


def _is_prefix_match(path: str, prefix: str) -> bool:
    return path == prefix.rstrip("/") or path.startswith(prefix)


def _classify_extra_paths(
    paths: list[str],
    *,
    files: list[str],
    always_allowed: tuple[str, ...],
    mode: str,
    verify_artifacts: str,
    unit: str | None,
    confidential: tuple[str, ...],
    memory_export_dir: str,
) -> tuple[list[str], list[str]]:
    """Sort cached-only paths into allowed and rejected (SPEC §7.4).

    ``mode`` is the bead's stage's ownership mode; this mirrors
    ``ownership.check``'s per-path rule exactly so the two never disagree on
    a path. It stays a separate copy because ``ownership.check`` derives its
    path list from git itself and has nowhere to take an arbitrary list of
    already-known paths instead.
    """
    rejected_prefixes = (
        *ownership_mod.ALWAYS_REJECTED,
        memory_export_dir.rstrip("/") + "/",
    )
    allowed: list[str] = []
    rejected: list[str] = []
    for path in paths:
        if any(_is_prefix_match(path, prefix) for prefix in rejected_prefixes):
            rejected.append(path)
            continue
        if ownership_mod._matches_confidential(path, confidential):
            rejected.append(path)
            continue
        if mode == "artifacts":
            scope = f"{verify_artifacts.rstrip('/')}/{unit}/" if unit else None
            if scope is not None and _is_prefix_match(path, scope):
                allowed.append(path)
            else:
                rejected.append(path)
            continue
        if mode == "none":
            allowed.append(path)
            continue
        if ownership_mod._matches_any(path, files) or any(
            _is_prefix_match(path, prefix) for prefix in always_allowed
        ):
            allowed.append(path)
        else:
            rejected.append(path)
    return allowed, rejected


def _index_paths(worktree: Path) -> set[str]:
    """Paths with an index entry (SPEC §7.1 step 10)."""
    try:
        return set(_git_nul(worktree, "ls-files", "-z", "--cached"))
    except RuntimeError:
        return set()


def _stage_and_commit(
    worktree: Path, bead_id: str, summary: str, paths: list[str]
) -> tuple[bool, str]:
    """Stage and commit exactly ``paths`` in three calls (SPEC §7.1 step 10).

    A path may exist nowhere (a rename source or a staged-then-deleted
    file), and git rejects a pathspec matching nothing, so stage only
    paths on disk or in the index, select the staged ones that differ
    from HEAD, and commit exactly those. Returns (committed, head).
    """
    if not paths:
        return False, _head_commit(worktree)
    specs = [_literal(p) for p in paths]
    index = _index_paths(worktree)
    addable = [
        p for p in paths
        if os.path.lexists(worktree / p) or p in index
    ]
    if addable:
        _git(worktree, "add", "-A", "--", *[_literal(p) for p in addable])
    selected = _git_nul(
        worktree, "diff", "--cached", "--name-only", "--no-renames",
        "-z", "HEAD", "--", *specs,
    )
    if not selected:
        return False, _head_commit(worktree)
    _git(
        worktree,
        "-c",
        "user.email=helios@localhost",
        "-c",
        "user.name=helios",
        "commit",
        "-m",
        f"{bead_id}: {summary}",
        "--",
        *[_literal(p) for p in selected],
    )
    return True, _head_commit(worktree)


def _exit_code_for(
    *,
    gate: str,
    execution_status: envelope_mod.ExecutionStatus,
    report: envelope_mod.AgentReport | None,
    checks_passed: bool,
) -> int:
    """Exit codes of SPEC §7.1, keyed on the bead's stage ``gate``: 0 done under
    ``report``, verified under ``verdict``, or a finalized attempt under ``none``
    (judged on neither report status nor verdict); 3 waiting; 4 failure; 5 check.
    """
    if execution_status is not envelope_mod.ExecutionStatus.COMPLETED:
        return 4
    if not checks_passed:
        return 5
    if gate == "none":
        return 0
    if report is None:
        return 4
    if gate == "verdict":
        verdict = envelope_mod.overall_verdict(list(report.findings))
        return 0 if verdict is envelope_mod.Verdict.VERIFIED else 3
    return 0 if report.status is envelope_mod.WorkStatus.DONE else 3


def _correct_run_state(
    *,
    execution_status: envelope_mod.ExecutionStatus,
    report: envelope_mod.AgentReport | None,
    checks_passed: bool,
) -> str:
    """The ``bd set-state run=`` value of SPEC §7.5."""
    if (
        execution_status is not envelope_mod.ExecutionStatus.COMPLETED
        or not checks_passed
    ):
        return "failed"
    if report is not None and report.status is envelope_mod.WorkStatus.BLOCKED:
        return "blocked"
    return "waiting"


def _event_type_for(
    *,
    execution_status: envelope_mod.ExecutionStatus,
    report: envelope_mod.AgentReport | None,
    checks_passed: bool,
) -> str:
    """Map the outcome to one SPEC §9.3 event type."""
    if execution_status is not envelope_mod.ExecutionStatus.COMPLETED or not checks_passed:
        return "failed"
    if report is None:
        return "failed"
    if report.status is envelope_mod.WorkStatus.DONE:
        return "completed"
    if report.status is envelope_mod.WorkStatus.NEEDS_INPUT:
        return "needs_input"
    if report.status is envelope_mod.WorkStatus.NEEDS_REVIEW:
        return "needs_review"
    return "failed"


def _missing_skill(hub: Path, kind: str) -> str | None:
    """The preflight error when ``skills/<kind>/SKILL.md`` is absent (SPEC §7.1)."""
    if not (hub / "skills" / kind / "SKILL.md").is_file():
        return f"skills/{kind}/SKILL.md does not exist in the hub"
    return None


def _stored_execution(attempt_dir: Path) -> envelope_mod.ExecutionStatus | None:
    """A stored step 8 status from state.json, else the envelope (SPEC §8.4)."""
    try:
        data = jsonio.loads((attempt_dir / "state.json").read_text())
        value = data.get("execution_status")
        if value is not None:
            return envelope_mod.ExecutionStatus(value)
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    try:
        env = jsonio.loads((attempt_dir / "envelope.json").read_text())
        value = env.get("execution_status")
        if value is not None:
            return envelope_mod.ExecutionStatus(value)
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return None


def _read_report_bytes(path: Path) -> tuple[dict | None, str | None, bytes | None]:
    """Read a report file: (candidate, error, raw bytes).

    Missing gives (None, None, None); present but bad gives (None, error,
    raw bytes).
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None, None, None
    try:
        parsed = jsonio.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
        return None, f"report is not valid JSON: {exc}", raw
    if not isinstance(parsed, dict):
        return None, "report JSON is not an object", raw
    return parsed, None, raw


@dataclass
class _Capture:
    """The step 8 capture decision (SPEC §6.2).

    Fresh attempts choose between the native channel and the worktree
    report file and persist the chosen bytes; resume and recovery read
    only the captured copy (SPEC §6.2 step 5).
    """

    candidate: dict | None
    raw_error: str | None
    store_bytes: bytes | None
    force: envelope_mod.ExecutionStatus | None
    notes: list[str]


def _capture_fresh(
    native_structured: dict | None, report_path: Path
) -> _Capture:
    """Choose the report for a fresh attempt (SPEC §6.2 steps 3-5)."""
    file_candidate, file_error, raw = _read_report_bytes(report_path)
    notes: list[str] = []
    if native_structured is not None:
        if file_candidate is not None and file_candidate != native_structured:
            notes.append("native result differs from report file; using native")
        elif file_error is not None:
            notes.append(f"report file unreadable ({file_error}); using native")
        store = (
            jsonio.dumps(native_structured, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        return _Capture(native_structured, None, store, None, notes)
    if file_candidate is not None:
        return _Capture(file_candidate, None, raw, None, notes)
    if file_error is not None:
        return _Capture(None, file_error, raw, None, notes)
    return _Capture(None, None, None, None, notes)


def _capture_resume(
    attempt_dir: Path,
    legacy_path: Path | None,
    stored: envelope_mod.ExecutionStatus | None,
) -> _Capture:
    """Read the captured copy; legacy worktree fallback without status.

    A stored ``completed`` whose captured report is missing or invalid
    keeps ``completed``; the failure surfaces as a helios ``report``
    check (SPEC §8.4).
    """
    captured = attempt_dir / "report.json"
    candidate, error, raw = _read_report_bytes(captured)
    store: bytes | None = None
    if raw is None and stored is None and legacy_path is not None:
        candidate, error, store = _read_report_bytes(legacy_path)
    return _Capture(candidate, error, store, stored, [])


@dataclass
class _FinishArgs:
    hub: Path
    config: config_mod.Config
    beads: beads_mod.BeadsLike
    bead: beads_mod.Bead
    harness_name: str
    attempt: attempt_mod.Attempt
    worktree_path: Path
    base_commit: str
    input_hashes: dict[str, str]
    started_at: str
    notes: list[str]
    steered: list[str] = field(default_factory=list)
    launch_finished_at: str | None = None


def _apply_writeback_once(
    beads: beads_mod.BeadsLike, bead_id: str, plan: beads_mod.Writeback, final_state: str
) -> None:
    """Apply a write-back plan with exactly one ``set-state`` call (SPEC §7.5)."""
    from dataclasses import replace

    if not plan.close and plan.run_state != final_state:
        plan = replace(plan, run_state=final_state)
    beads_mod.apply_writeback(beads, bead_id, plan)


def _attempt_trace(
    args: _FinishArgs,
    *,
    bead: beads_mod.Bead,
    execution: envelope_mod.ExecutionStatus,
    terminal: str,
    native_session: str | None,
    output_commit: str | None,
    model: str | None,
    validate_started_at: str,
    validate_finished_at: str,
    checks: list[envelope_mod.Check],
    check_spans: list[tuple[str, str, str]],
    commit_span: tuple[str, str] | None,
    attempt_finished_at: str,
) -> telemetry_mod.Span:
    """Build the attempt trace of SPEC section 21: root span ``attempt``, children
    ``launch``, ``validate``, one ``check.<name>`` per check performed, ``commit``,
    in that order. That is the order of section 7.1, which parses and classifies at
    step 8 before the checks at step 9, so the emitted order agrees with the span
    timestamps. A child is included only when its phase actually ran, so an
    attempt that never launched carries no ``launch``, ``check.*`` or ``commit``
    spans.
    """
    children: list[telemetry_mod.Span] = []
    if args.launch_finished_at is not None:
        children.append(
            telemetry_mod.Span(name="launch", start=args.started_at, end=args.launch_finished_at)
        )
    children.append(
        telemetry_mod.Span(name="validate", start=validate_started_at, end=validate_finished_at)
    )
    check_times = {name: (start, end) for name, start, end in check_spans}
    for check in checks:
        times = check_times.get(check.name)
        if times is None:
            continue
        attrs: dict[str, telemetry_mod.AttrValue] = {
            "check.name": check.name,
            "passed": check.passed,
        }
        if check.exit_code is not None:
            attrs["exit_code"] = check.exit_code
        children.append(
            telemetry_mod.Span(
                name=f"check.{check.name}", start=times[0], end=times[1], attributes=attrs
            )
        )
    if commit_span is not None:
        children.append(telemetry_mod.Span(name="commit", start=commit_span[0], end=commit_span[1]))
    attributes: dict[str, telemetry_mod.AttrValue] = {
        "bead.id": bead.id,
        "bead.kind": bead.kind,
        "harness": args.harness_name,
        "execution_status": execution.value,
        "terminal_state": terminal,
        "base_commit": args.base_commit,
    }
    if model is not None:
        attributes["model"] = model
    if native_session is not None:
        attributes["session_id"] = native_session
    if output_commit is not None:
        attributes["output_commit"] = output_commit
    return telemetry_mod.Span(
        name="attempt",
        start=args.started_at,
        end=attempt_finished_at,
        attributes=attributes,
        children=tuple(children),
    )


def _finish_attempt(
    args: _FinishArgs,
    *,
    capture: _Capture,
    native_session: str | None,
    native_error: str | None,
    proc_exit: int | None,
    interrupted: bool,
    timed_out: bool,
    launch_failed: bool,
    transition_from: str | None,
    launched: bool = True,
    unlaunched_detail: str = "interrupted",
) -> int:
    """Validate, check, commit, envelop, write back and finalize one attempt."""
    bead = args.bead
    stage = args.config.stages.get(bead.kind)
    attempt_dir = args.attempt.dir
    n = args.attempt.n
    attempt_id = args.attempt.attempt_id
    validate_started_at = attempt_mod.utc_now()

    if capture.store_bytes is not None:
        try:
            (attempt_dir / "report.json").write_bytes(capture.store_bytes)
        except OSError:
            pass
    report: envelope_mod.AgentReport | None = None
    report_error = capture.raw_error
    if capture.candidate is not None:
        try:
            report = envelope_mod.AgentReport.model_validate(capture.candidate)
            report_error = None
        except Exception as exc:
            report_error = f"report fails schema: {exc}"
    notes = [*args.notes, *capture.notes]
    has_candidate = report is not None or report_error is not None
    if capture.force is not None:
        execution = capture.force
    else:
        execution = classify_execution(
            interrupted=interrupted,
            timed_out=timed_out,
            launch_failed=launch_failed,
            exit_code=proc_exit,
            native_error=native_error,
            has_candidate=has_candidate,
            report_valid=report is not None,
        )
    if execution is envelope_mod.ExecutionStatus.COMPLETED and (
        (proc_exit is not None and proc_exit != 0) or native_error is not None
    ):
        notes.append(
            "completed with a valid report; "
            f"native issue ignored ({native_error or f'exit code {proc_exit}'})"
        )
    finished_at = attempt_mod.utc_now()
    validate_finished_at = finished_at

    terminal = {
        envelope_mod.ExecutionStatus.INTERRUPTED: "interrupted",
        envelope_mod.ExecutionStatus.TIMED_OUT: "timed_out",
        envelope_mod.ExecutionStatus.LAUNCH_FAILED: "launch_failed",
    }.get(execution, "native_completed" if proc_exit == 0 else "crashed")
    if launch_failed:
        terminal = "launch_failed"
    if transition_from is not None:
        attempt_mod.transition(
            attempt_dir,
            terminal,
            execution_status=execution.value,
            session_id=native_session,
        )

    checks_dir = attempt_dir / "checks"
    checks: list[envelope_mod.Check] = []
    check_spans: list[tuple[str, str, str]] = []
    commit_span: tuple[str, str] | None = None
    if (
        execution is envelope_mod.ExecutionStatus.COMPLETED
        and report is None
    ):
        if capture.candidate is None:
            problem = (
                "captured report is missing"
                if capture.raw_error is None
                else f"captured report invalid: {capture.raw_error}"
            )
        else:
            problem = f"captured report invalid: {report_error}"
        checks.append(
            envelope_mod.Check(name="report", passed=False, detail=problem)
        )
    if not launched:
        if bead.test:
            checks.append(
                envelope_mod.Check(name="test", passed=False, detail=unlaunched_detail)
            )
        for entry in config_mod.effective_checks(args.config.project):
            if entry.when not in ("run", "both") or not entry.command:
                continue
            checks.append(
                envelope_mod.Check(
                    name=entry.name, passed=False, detail=unlaunched_detail
                )
            )
        checks.append(
            envelope_mod.Check(
                name="ownership", passed=False, detail=unlaunched_detail
            )
        )
        checks_passed = False
        output_commit: str | None = None
    else:
        if bead.test:
            _check_start = attempt_mod.utc_now()
            passed, code, detail = _run_shell(
                bead.test, args.worktree_path, checks_dir / "test.log",
                f"{attempt_dir}:check:test",
            )
            check_spans.append(("test", _check_start, attempt_mod.utc_now()))
            checks.append(
                envelope_mod.Check(
                    name="test",
                    passed=passed,
                    command=bead.test,
                    exit_code=code,
                    detail=detail,
                    log_path="checks/test.log",
                )
            )
        for entry in config_mod.effective_checks(args.config.project):
            if entry.when not in ("run", "both") or not entry.command:
                continue
            _check_start = attempt_mod.utc_now()
            passed, code, detail = _run_shell(
                entry.command, args.worktree_path, checks_dir / f"{entry.name}.log",
                f"{attempt_dir}:check:{entry.name}",
            )
            check_spans.append((entry.name, _check_start, attempt_mod.utc_now()))
            checks.append(
                envelope_mod.Check(
                    name=entry.name,
                    passed=passed,
                    command=entry.command,
                    exit_code=code,
                    detail=detail,
                    log_path=f"checks/{entry.name}.log",
                )
            )
        _ownership_start = attempt_mod.utc_now()
        ownership = ownership_mod.check(
            worktree=args.worktree_path,
            base_commit=args.base_commit,
            files=list(bead.files),
            always_allowed=args.config.project.always_allowed,
            mode=stage.ownership,
            verify_artifacts=args.config.project.verify_artifacts,
            unit=bead.unit,
            confidential=args.config.project.confidential,
            memory_export_dir=args.config.memory.export_dir,
            link_into_worktrees=args.config.project.link_into_worktrees,
        )
        try:
            cached_base = _git_nul(
                args.worktree_path, "diff", "--cached", "--name-only",
                "--no-renames", "-z", args.base_commit,
            )
        except RuntimeError:
            cached_base = []
        try:
            cached_head = _git_nul(
                args.worktree_path, "diff", "--cached", "--name-only",
                "--no-renames", "-z", "HEAD",
            )
        except RuntimeError:
            cached_head = []
        seen = set(ownership.allowed) | set(ownership.rejected)
        extra = [p for p in [*cached_base, *cached_head] if p not in seen]
        if extra:
            extra_allowed, extra_rejected = _classify_extra_paths(
                extra,
                files=list(bead.files),
                always_allowed=args.config.project.always_allowed,
                mode=stage.ownership,
                verify_artifacts=args.config.project.verify_artifacts,
                unit=bead.unit,
                confidential=args.config.project.confidential,
                memory_export_dir=args.config.memory.export_dir,
            )
            ownership = ownership_mod.OwnershipResult(
                allowed=tuple([*ownership.allowed, *extra_allowed]),
                rejected=tuple([*ownership.rejected, *extra_rejected]),
                detail=(
                    f"rejected: {', '.join([*ownership.rejected, *extra_rejected])}"
                    if [*ownership.rejected, *extra_rejected]
                    else "all paths allowed"
                ),
            )
        check_spans.append(("ownership", _ownership_start, attempt_mod.utc_now()))
        checks.append(
            envelope_mod.Check(
                name="ownership",
                passed=ownership.passed,
                detail=ownership.detail,
            )
        )
        checks_passed = all(c.passed for c in checks)

        if not ownership.passed:
            output_commit = None
        else:
            summary = report.summary if report is not None else execution.value
            _commit_start = attempt_mod.utc_now()
            _stage_and_commit(
                args.worktree_path, bead.id, summary, list(ownership.allowed)
            )
            output_commit = _head_commit(args.worktree_path)
            commit_span = (_commit_start, attempt_mod.utc_now())

    harness_cfg = args.config.harness.get(args.harness_name)
    if args.config.telemetry.enabled:
        note = telemetry_mod.export(
            args.config.telemetry,
            _attempt_trace(
                args,
                bead=bead,
                execution=execution,
                terminal=terminal,
                native_session=native_session,
                output_commit=output_commit,
                model=harness_cfg.model if harness_cfg else None,
                validate_started_at=validate_started_at,
                validate_finished_at=validate_finished_at,
                checks=checks,
                check_spans=check_spans,
                commit_span=commit_span,
                attempt_finished_at=attempt_mod.utc_now(),
            ),
        )
        if note is not None:
            notes.append(note)
    verdict: str | None = None
    if report is not None and stage.gate == "verdict":
        overall = envelope_mod.overall_verdict(list(report.findings))
        verdict = overall.value if overall is not None else None
    if execution is envelope_mod.ExecutionStatus.COMPLETED and report is None:
        # SPEC §8.4 keeps a stored completed whose captured report went bad;
        # the contracts model cannot hold completed without a report, so the
        # envelope is written unvalidated with the failing report check.
        envelope = envelope_mod.Envelope.model_construct(
            schema_version="1",
            task_id=bead.id,
            attempt=n,
            attempt_id=attempt_id,
            kind=bead.kind,
            harness=args.harness_name,
            model=harness_cfg.model if harness_cfg else None,
            session_id=native_session,
            started_at=args.started_at,
            finished_at=finished_at,
            base_commit=args.base_commit,
            output_commit=output_commit,
            input_hashes=dict(args.input_hashes),
            execution_status=execution,
            exit_code=proc_exit,
            report=None,
            report_error=report_error,
            checks=checks,
            artifacts=[],
            steered=list(args.steered),
            notes=notes,
        )
    else:
        envelope = envelope_mod.Envelope(
            task_id=bead.id,
            attempt=n,
            attempt_id=attempt_id,
            kind=bead.kind,
            harness=args.harness_name,  # type: ignore[arg-type]
            model=harness_cfg.model if harness_cfg else None,
            session_id=native_session,
            started_at=args.started_at,
            finished_at=finished_at,
            base_commit=args.base_commit,
            output_commit=output_commit,
            input_hashes=dict(args.input_hashes),
            execution_status=execution,
            exit_code=proc_exit,
            report=report,
            report_error=report_error,
            checks=checks,
            steered=list(args.steered),
            notes=notes,
        )
    (attempt_dir / "envelope.json").write_text(
        envelope.model_dump_json(indent=2, exclude_none=False) + "\n"
    )
    attempt_mod.transition(
        attempt_dir,
        "validated" if report is not None else "invalid",
        execution_status=execution.value,
    )

    plan = beads_mod.plan_writeback(
        attempt_id=attempt_id,
        gate=stage.gate,
        harness=args.harness_name,
        session_id=native_session,
        worktree=str(args.worktree_path),
        attempt=n,
        execution_status=execution.value,
        verdict=verdict,
        output_commit=output_commit,
        report_status=report.status.value if report else None,
        report_summary=report.summary if report else execution.value,
        learned=list(report.learned) if report else [],
        missing_context=list(report.missing_context) if report else [],
        followups=list(report.followups) if report else [],
        question=report.question if report else None,
        checks_passed=checks_passed,
    )
    _apply_writeback_once(
        args.beads,
        bead.id,
        plan,
        _correct_run_state(
            execution_status=execution, report=report, checks_passed=checks_passed
        ),
    )
    attempt_mod.transition(attempt_dir, "finalized")
    session = f"{args.harness_name}:{native_session}" if native_session else None
    events_mod.append(
        args.hub,
        source="helios",
        type=_event_type_for(
            execution_status=execution, report=report, checks_passed=checks_passed
        ),
        bead=bead.id,
        attempt=attempt_id,
        session=session,
        detail=report.summary if report else execution.value,
    )
    events_mod.append(
        args.hub,
        source="helios",
        type="finalized",
        bead=bead.id,
        attempt=attempt_id,
        session=session,
        detail=execution.value,
    )
    if _INTERRUPT.is_set():
        return 4
    return _exit_code_for(
        gate=stage.gate,
        execution_status=execution,
        report=report,
        checks_passed=checks_passed,
    )


def _finalize_unlaunched_lookup_failure(
    finish_args: _FinishArgs, exc: MemoryLookupError
) -> int:
    """An attempt already allocated when a memory lookup then failed (SPEC §7.1
    step 6, round-1-fix item 2): print the message, finalize the attempt as
    any launch that never started (SPEC §8: no checks run, nothing staged or
    committed), and refuse with exit 2. Write-back never closes the bead,
    since the checks are recorded as not run.
    """
    print(str(exc), file=sys.stderr)
    attempt_mod.transition(
        finish_args.attempt.dir,
        "launch_failed",
        execution_status=envelope_mod.ExecutionStatus.LAUNCH_FAILED.value,
    )
    _finish_attempt(
        finish_args,
        capture=_Capture(None, None, None, None, []),
        native_session=None,
        native_error=None,
        proc_exit=None,
        interrupted=False,
        timed_out=False,
        launch_failed=True,
        transition_from=None,
        launched=False,
        unlaunched_detail="memory lookup failed",
    )
    return 2


def _gather_input_texts(
    hub: Path,
    config: config_mod.Config,
    beads: beads_mod.BeadsLike,
    bead: beads_mod.Bead,
) -> tuple[dict[str, object], list[tuple[str, str]], dict[str, str], str | None]:
    """Read a bead's persisted, un-prompted inputs (SPEC §7.1 step 6, §7.2).

    Returns the bead JSON fields exactly as the prompt's ``Bead`` section
    renders them, each docs entry through ``prompt_mod.read_doc``, each
    memory value resolved through the memory backend keyed by
    ``config.memory.backend`` (SPEC §13), and the unit file text when the
    bead has a unit. Shared by prompt assembly (``_assemble_inputs``) and
    the verify-evidence staleness recompute a merge runs (SPEC §8.5,
    ``recompute_input_hashes``), so there is one copy of this logic.
    """
    bead_map: dict[str, object] = {
        "id": bead.id,
        "title": bead.title,
        "description": bead.description,
        "kind": bead.kind,
        "unit": bead.unit,
        "accept": bead.accept,
        "files": list(bead.files),
        "test": bead.test,
    }
    memories: dict[str, str] = {}
    if bead.memories:
        backend = _memory_backend(beads, config)
        for key in bead.memories:
            try:
                memories[key] = backend.read(key).body
            except (KeyError, ValueError, RuntimeError, OSError) as exc:
                # No "" fallback (round-1-fix item 2): preflight's lookup
                # (SPEC §13) is the real gate; a value it reported as present
                # that cannot actually be read here is a refusal, exactly
                # like a lookup failure at preflight time, not a silently
                # empty section in the prompt.
                raise MemoryLookupError(
                    f"helios: memory lookup failed for {key}: {exc}"
                ) from exc
    docs_texts: list[tuple[str, str]] = []
    for entry in bead.docs:
        try:
            _, text = prompt_mod.read_doc(hub, entry)
        except (OSError, KeyError):
            text = ""
        docs_texts.append((entry, text))
    unit_text: str | None = None
    if bead.unit:
        unit_path = hub / config.project.units / f"{bead.unit}.md"
        try:
            unit_text = unit_path.read_text()
        except OSError:
            unit_text = None
    return bead_map, docs_texts, memories, unit_text


def recompute_input_hashes(
    hub: Path,
    config: config_mod.Config,
    beads: beads_mod.BeadsLike,
    bead: beads_mod.Bead,
) -> dict[str, str]:
    """Recompute a bead's persisted input hashes the same way a run computes
    them (SPEC §7.1 step 6), for the merge staleness check (SPEC §8.5).

    Never returns a ``prompt`` key: recomputing the actual prompt text needs
    an attempt's worktree, branch and report path, none of which exist at
    merge time, and SPEC §8.5 exempts ``prompt`` from the comparison anyway.
    """
    bead_map, docs_texts, memories, unit_text = _gather_input_texts(hub, config, beads, bead)
    bead_json = json.dumps(bead_map, indent=2, sort_keys=True)
    hashes = compute_input_hashes(
        prompt="", bead_json=bead_json, docs=docs_texts, memories=memories, unit_text=unit_text
    )
    del hashes["prompt"]
    return hashes


def _assemble_inputs(
    *,
    hub: Path,
    config: config_mod.Config,
    beads: beads_mod.BeadsLike,
    bead: beads_mod.Bead,
    worktree_path: Path,
    branch: str,
    attempt_id: str,
    report_path: Path,
) -> tuple[str, dict[str, str], str, list[tuple[str, str]], dict[str, str], str | None]:
    """Assemble the prompt and its hashed inputs (SPEC §7.2, §8.3)."""
    skills_dir = hub / "skills"
    agents_path = hub / "AGENTS.md"
    bead_map, docs_texts, memories, unit_text = _gather_input_texts(hub, config, beads, bead)
    report_schema = envelope_mod.schema_text("agent-report")
    prompt = prompt_mod.assemble(
        kind=bead.kind,
        skills_dir=skills_dir,
        agents_path=agents_path,
        bead=bead_map,
        docs=list(bead.docs),
        hub=hub,
        memories=memories,
        worktree=worktree_path,
        branch=branch,
        attempt_id=attempt_id,
        report_path=report_path,
        report_schema=report_schema,
        inject_cap_bytes=config.memory.inject_cap_bytes,
    )
    bead_json = json.dumps(bead_map, indent=2, sort_keys=True)
    hashes = compute_input_hashes(
        prompt=prompt,
        bead_json=bead_json,
        docs=docs_texts,
        memories=memories,
        unit_text=unit_text,
    )
    return prompt, hashes, bead_json, docs_texts, memories, unit_text


class _BeadLock:
    """Exclusive non-blocking flock on ``<runs>/<bead>/lock`` (SPEC §8.3)."""

    def __init__(self, hub: Path, runs_rel: str, bead_id: str) -> None:
        self.path = hub / runs_rel / bead_id / "lock"
        self.fd: int | None = None
        self.created = False

    def acquire(self, *, create: bool) -> bool:
        """Take the lock; without ``create`` never make the file."""
        if not create and not self.path.exists():
            return True
        if create:
            self.created = not self.path.exists()
            self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        except OSError:
            return False
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
            return False
        return True

    def release(self, *, remove_if_created: bool = False) -> None:
        """Release the lock, optionally removing a file this run made."""
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        if remove_if_created and self.created:
            try:
                self.path.unlink()
            except OSError:
                pass


def _verify_start_kwargs(
    hub: Path,
    worktrees_rel: str,
    beads: beads_mod.BeadsLike,
    bead: beads_mod.Bead,
    stage: stageset.StageSpec,
) -> dict[str, str]:
    """``start=`` for ``worktree.prepare``: a bead of a stage declaring ``verifies``
    starts at its parent's ``output_commit`` metadata (SPEC §7.3). An existing
    worktree ignores ``start`` (``worktree.prepare`` just reuses it), and any
    stage with no ``verifies`` keeps ``worktree.prepare``'s own ``main`` default.

    Preflight (``_check_verify_worktree_start``) already refuses a fresh
    verify worktree with no such metadata in every production path (it is
    always wired with the real ``bead_show``), so a missing or unreadable
    parent here raises ``VerifyStartError`` rather than quietly falling back
    to ``main`` (round-1-fix item 2); it is defense in depth for a caller
    that bypasses preflight.
    """
    if stage.verifies is None or not bead.parent:
        return {}
    if (hub / worktrees_rel / bead.id).exists():
        return {}
    try:
        commit = beads.show(bead.parent).metadata.get("output_commit")
    except Exception as exc:
        raise VerifyStartError(
            f"helios: verify worktree for {bead.id}: parent {bead.parent} lookup failed: {exc}"
        ) from exc
    if not isinstance(commit, str) or not commit:
        raise VerifyStartError(
            f"helios: verify worktree for {bead.id}: "
            f"parent {bead.parent} has no output_commit metadata"
        )
    return {"start": commit}


def _refuse_live(bead_id: str, attempt_id: str | None) -> int:
    """Print the attach and stop commands and refuse with exit 2 (SPEC §8.4)."""
    print(
        f"attempt {attempt_id} is still running; "
        f"use `helios attach {bead_id}` or `helios stop {bead_id}`",
        file=sys.stderr,
    )
    return 2


def _dry_run_one(
    bead_id: str,
    *,
    hub: Path,
    beads: beads_mod.BeadsLike,
    bead: beads_mod.Bead,
    config: config_mod.Config,
    harness_name: str,
    harness: Harness,
    effective_timeout: int,
) -> int:
    """Print the dry-run plan; create, move or write nothing (SPEC §7.1)."""
    latest = attempt_mod.latest_state(hub, config.project.runs, bead_id)
    if latest is None:
        action = "new"
    else:
        action = attempt_mod.classify_recovery(
            latest.get("state"),
            pid_alive=attempt_mod.is_pid_alive(latest.get("pid"), latest.get("pid_start")),
        )
    if action == "refuse":
        return _refuse_live(bead_id, latest.get("attempt_id") if latest else None)
    print(f"recovery: {action}")
    worktree_path = hub / config.project.worktrees / bead_id
    branch = worktree_mod.branch_name(bead_id)
    numbers = attempt_mod.existing_attempts(hub / config.project.runs / bead_id)
    prompt_size: int | None = None
    attempt_path: Path | None = None
    if action == "resume" and numbers:
        candidate_path = hub / config.project.runs / bead_id / f"attempt-{numbers[-1]}"
        try:
            prompt_size = len((candidate_path / "prompt.md").read_bytes())
        except OSError:
            prompt_size = None
        else:
            attempt_path = candidate_path
    if attempt_path is None:
        next_n = (numbers[-1] if numbers else 0) + 1
        attempt_path = hub / config.project.runs / bead_id / f"attempt-{next_n}"
    else:
        next_n = numbers[-1] if numbers else 1
    report_path = attempt_mod.worktree_report_path(worktree_path, next_n)
    try:
        prompt, _, _, _, _, _ = _assemble_inputs(
            hub=hub,
            config=config,
            beads=beads,
            bead=bead,
            worktree_path=worktree_path,
            branch=branch,
            attempt_id=f"{bead_id}#{next_n}",
            report_path=report_path,
        )
    except MemoryLookupError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    harness_cfg = config.harness.get(harness_name)
    spec = LaunchSpec(
        bead=bead_id,
        attempt=next_n,
        worktree=worktree_path,
        prompt=prompt,
        report_path=report_path,
        report_schema_path=attempt_path / "report-schema.json",
        raw_dir=attempt_path / "raw",
        model=harness_cfg.model if harness_cfg else None,
        effort=harness_cfg.effort if harness_cfg else None,
        timeout_s=effective_timeout,
        server_url=harness_cfg.server_url if harness_cfg else None,
        extra_args=tuple(harness_cfg.extra_args) if harness_cfg else (),
        env=dict(os.environ),
    )
    print(f"harness: {harness_name}")
    print(f"argv: {' '.join(harness.argv(spec))}")
    print(f"worktree: {worktree_path}")
    print(f"attempt: {attempt_path}")
    if prompt_size is None:
        prompt_size = len(prompt.encode("utf-8"))
    print(f"prompt_bytes: {prompt_size}")
    return 0


def _write_recovery_envelope(
    *,
    hub: Path,
    config: config_mod.Config,
    bead: beads_mod.Bead,
    harness_name: str,
    attempt_dir: Path,
    n: int,
    attempt_id: str,
    execution: envelope_mod.ExecutionStatus,
    note: str,
) -> None:
    """Write the finalize-and-new envelope for an unlaunched attempt (SPEC §8.4)."""
    if (attempt_dir / "envelope.json").exists():
        return
    hashes: dict[str, str] = {}
    base_commit = ""
    try:
        saved = jsonio.loads((attempt_dir / "input.json").read_text())
        hashes = dict(saved.get("input_hashes") or {})
        base_commit = str(saved.get("base_commit") or "")
    except (OSError, json.JSONDecodeError, ValueError, AttributeError):
        pass
    harness_cfg = config.harness.get(harness_name)
    now = attempt_mod.utc_now()
    envelope = envelope_mod.Envelope(
        task_id=bead.id,
        attempt=n,
        attempt_id=attempt_id,
        kind=bead.kind,
        harness=harness_name,  # type: ignore[arg-type]
        model=harness_cfg.model if harness_cfg else None,
        session_id=None,
        started_at=now,
        finished_at=now,
        base_commit=base_commit,
        output_commit=None,
        input_hashes=hashes,
        execution_status=execution,
        exit_code=None,
        report=None,
        report_error=None,
        checks=[],
        notes=[note],
    )
    (attempt_dir / "envelope.json").write_text(
        envelope.model_dump_json(indent=2, exclude_none=False) + "\n"
    )


def run_one(
    bead_id: str,
    *,
    hub: Path,
    beads: beads_mod.BeadsLike,
    config: config_mod.Config | None = None,
    harness_override: str | None = None,
    timeout_s: int | None = None,
    again: bool = False,
    dry_run: bool = False,
    tee_stdout: bool = False,
) -> int:
    """Run one bead through launch, checks, commit and write-back (SPEC §7.1)."""
    if not _RUN_ACTIVE.is_set():
        _INTERRUPT.clear()
    return _run_one_inner(
        bead_id,
        hub=hub,
        beads=beads,
        config=config,
        harness_override=harness_override,
        timeout_s=timeout_s,
        again=again,
        dry_run=dry_run,
        tee_stdout=tee_stdout,
    )


def _run_one_inner(
    bead_id: str,
    *,
    hub: Path,
    beads: beads_mod.BeadsLike,
    config: config_mod.Config | None,
    harness_override: str | None,
    timeout_s: int | None,
    again: bool,
    dry_run: bool,
    tee_stdout: bool = False,
) -> int:
    """The body of :func:`run_one` under the run guard."""
    if _INTERRUPT.is_set():
        return 4
    hub = hub.resolve()
    cfg = config if config is not None else config_mod.load(hub)
    bead = beads.show(bead_id)
    skill_error = _missing_skill(hub, bead.kind)
    if skill_error is not None:
        print(f"preflight: {bead_id}: {skill_error}", file=sys.stderr)
        return 2
    if bead.status == "closed":
        print(f"preflight: {bead_id}: bead is closed", file=sys.stderr)
        return 2
    harness_name = config_mod.harness_for_kind(
        cfg, bead.kind, author=bead.author, override=harness_override
    )
    from helios.harness import get as harness_get

    harness = harness_get(harness_name)
    harness_cfg = cfg.harness.get(harness_name)
    effective_timeout = (
        timeout_s
        if timeout_s is not None
        else (harness_cfg.timeout_s if harness_cfg else 3600)
    )

    lock = _BeadLock(hub, cfg.project.runs, bead_id)
    if not lock.acquire(create=not dry_run):
        latest_attempt = attempt_mod.latest_state(hub, cfg.project.runs, bead_id)
        return _refuse_live(
            bead_id,
            latest_attempt.get("attempt_id") if latest_attempt else bead_id,
        )
    try:
        if dry_run:
            return _dry_run_one(
                bead_id,
                hub=hub,
                beads=beads,
                bead=bead,
                config=cfg,
                harness_name=harness_name,
                harness=harness,
                effective_timeout=effective_timeout,
            )

        latest = attempt_mod.latest_state(hub, cfg.project.runs, bead_id)
        if latest is not None:
            action = attempt_mod.classify_recovery(
                latest.get("state"),
                pid_alive=attempt_mod.is_pid_alive(latest.get("pid"), latest.get("pid_start")),
            )
            if action == "refuse":
                return _refuse_live(bead_id, latest.get("attempt_id"))
            if action == "resume":
                return _resume_latest(
                    bead_id, hub=hub, beads=beads, config=cfg, harness_name=harness_name
                )
            if action in ("finalize_and_new", "crash_and_new"):
                old_n = _attempt_n(latest.get("attempt_id"), bead_id)
                old_dir = attempt_mod.attempt_dir(hub, cfg.project.runs, bead_id, old_n)
                if old_dir.is_dir():
                    try:
                        if action == "crash_and_new":
                            # A stop was requested but helios died before it
                            # classified the attempt: recovery reads the same
                            # marker file so the outcome is interrupted, not
                            # crashed (SPEC §4.4 point 2, §8.4).
                            fallback = (
                                envelope_mod.ExecutionStatus.INTERRUPTED
                                if (old_dir / "stop-requested").exists()
                                else envelope_mod.ExecutionStatus.CRASHED
                            )
                            attempt_mod.transition(
                                old_dir, fallback.value, execution_status=fallback.value
                            )
                            stored = _stored_execution(old_dir)
                            _write_recovery_envelope(
                                hub=hub, config=cfg, bead=bead,
                                harness_name=harness_name, attempt_dir=old_dir,
                                n=old_n,
                                attempt_id=str(latest.get("attempt_id") or f"{bead_id}#{old_n}"),
                                execution=stored or fallback,
                                note="recovery: allocated with no live process",
                            )
                        else:
                            stored = _stored_execution(old_dir)
                            old_state = str(latest.get("state") or "")
                            try:
                                status = stored or envelope_mod.ExecutionStatus(old_state)
                            except ValueError:
                                status = envelope_mod.ExecutionStatus.CRASHED
                            _write_recovery_envelope(
                                hub=hub, config=cfg, bead=bead,
                                harness_name=harness_name, attempt_dir=old_dir,
                                n=old_n,
                                attempt_id=str(latest.get("attempt_id") or f"{bead_id}#{old_n}"),
                                execution=status,
                                note=f"recovery: finalized {old_state} without launch",
                            )
                        attempt_mod.transition(old_dir, "finalized")
                    except (OSError, ValueError, KeyError):
                        pass

        try:
            verify_kwargs = _verify_start_kwargs(
                hub, cfg.project.worktrees, beads, bead, cfg.stages.get(bead.kind)
            )
        except VerifyStartError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        # Once preflight has passed and before launch: a bead that does not
        # close (SPEC §7.5) would otherwise stay `open`, a SPEC §11 `next`
        # candidate that `helios run` would launch again. Never for
        # --dry-run (handled above) and never for a closed bead (checked at
        # the top of this function), so this only ever moves open -> in
        # progress; setting it again is a no-op (item 9).
        if bead.status != "in_progress":
            beads.set_status(bead_id, "in_progress")

        info = worktree_mod.prepare(
            hub=hub,
            bead=bead_id,
            worktrees=cfg.project.worktrees,
            link_into_worktrees=cfg.project.link_into_worktrees,
            again=again,
            **verify_kwargs,
        )
        if _INTERRUPT.is_set():
            return 4
        worktree_path = info.path
        base_commit = info.base_commit
        attempt_obj, alloc_notes = attempt_mod.allocate(
            hub=hub, runs_rel=cfg.project.runs, bead=bead_id, worktree=worktree_path
        )
        report_path = attempt_mod.worktree_report_path(worktree_path, attempt_obj.n)
        try:
            prompt, hashes, _, _, _, _ = _assemble_inputs(
                hub=hub,
                config=cfg,
                beads=beads,
                bead=bead,
                worktree_path=worktree_path,
                branch=info.branch,
                attempt_id=attempt_obj.attempt_id,
                report_path=report_path,
            )
        except MemoryLookupError as exc:
            finish_args = _FinishArgs(
                hub=hub, config=cfg, beads=beads, bead=bead, harness_name=harness_name,
                attempt=attempt_obj, worktree_path=worktree_path, base_commit=base_commit,
                input_hashes={}, started_at=attempt_mod.utc_now(), notes=list(alloc_notes),
            )
            return _finalize_unlaunched_lookup_failure(finish_args, exc)
        (attempt_obj.dir / "prompt.md").write_text(prompt)
        (attempt_obj.dir / "report-schema.json").write_text(
            envelope_mod.schema_text("agent-report")
        )
        _write_input_json(
            attempt_obj.dir / "input.json",
            {
                "harness": harness_name,
                "input_hashes": hashes,
                "base_commit": base_commit,
                "bead": bead_id,
            },
        )
        raw_dir = attempt_obj.dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        spec = LaunchSpec(
            bead=bead_id,
            attempt=attempt_obj.n,
            worktree=worktree_path,
            prompt=prompt,
            report_path=report_path,
            report_schema_path=attempt_obj.dir / "report-schema.json",
            raw_dir=raw_dir,
            model=harness_cfg.model if harness_cfg else None,
            effort=harness_cfg.effort if harness_cfg else None,
            timeout_s=effective_timeout,
            server_url=harness_cfg.server_url if harness_cfg else None,
            extra_args=tuple(harness_cfg.extra_args) if harness_cfg else (),
            env=dict(os.environ),
        )
        argv = harness.argv(spec)
        stdin_text = harness.stdin_text(spec)
        stdout_path = attempt_obj.dir / "stdout.jsonl"
        stderr_path = attempt_obj.dir / "stderr.log"
        started_at = attempt_mod.utc_now()
        env = dict(os.environ)
        env.update(
            {
                "HELIOS_BEAD": bead_id,
                "HELIOS_ATTEMPT": attempt_obj.attempt_id,
                "HELIOS_HARNESS": harness_name,
                "HELIOS_HUB": str(hub),
                "HELIOS_REPORT": str(report_path),
            }
        )
        run_key = str(attempt_obj.dir)
        launched_box = {"fired": False}

        def _record_launched(pid: int) -> None:
            launched_box["fired"] = True
            attempt_mod.transition(attempt_obj.dir, "launched", pid=pid)
            try:
                pid_start = attempt_mod.read_pid_start(pid)
            except Exception:
                pid_start = None
            attempt_mod.transition(attempt_obj.dir, "launched", pid=pid, pid_start=pid_start)
            events_mod.append(
                hub,
                source="helios",
                type="launched",
                bead=bead_id,
                attempt=attempt_obj.attempt_id,
                session=None,
                detail=f"harness {harness_name}",
            )

        stop_path = attempt_obj.dir / "stop-requested"
        proc_exit, timed_out, interrupted, launch_failed, _child = _launch_and_wait(
            argv,
            cwd=worktree_path,
            env=env,
            stdin_text=stdin_text,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            timeout_s=effective_timeout,
            run_key=run_key,
            on_launched=_record_launched,
            stop_path=stop_path,
            tee_stdout=tee_stdout,
        )
        launch_finished_at = attempt_mod.utc_now() if launched_box["fired"] else None
        # SPEC §4.4 point 2: tested once, after the group is gone, before parse.
        if not launch_failed and stop_path.exists():
            interrupted = True
        native = harness.parse(spec, proc_exit, stdout_path)
        if launch_failed:
            attempt_mod.transition(
                attempt_obj.dir,
                "launch_failed",
                execution_status=envelope_mod.ExecutionStatus.LAUNCH_FAILED.value,
                session_id=native.session_id,
            )
        finish_args = _FinishArgs(
            hub=hub,
            config=cfg,
            beads=beads,
            bead=bead,
            harness_name=harness_name,
            attempt=attempt_obj,
            worktree_path=worktree_path,
            base_commit=base_commit,
            input_hashes=hashes,
            started_at=started_at,
            notes=list(alloc_notes),
            launch_finished_at=launch_finished_at,
        )
        return _finish_attempt(
            finish_args,
            capture=_capture_fresh(native.structured, report_path),
            native_session=native.session_id,
            native_error=native.native_error,
            proc_exit=proc_exit,
            interrupted=interrupted,
            timed_out=timed_out,
            launch_failed=launch_failed,
            transition_from=None if launch_failed else "launched",
            launched=launched_box["fired"],
        )
    finally:
        lock.release(remove_if_created=dry_run)


def _attempt_n(attempt_id: str | None, bead_id: str) -> int:
    """Parse ``<bead>#<n>`` back to ``n``."""
    try:
        head, _, tail = (attempt_id or "").rpartition("#")
        if head == bead_id:
            return int(tail)
    except ValueError:
        pass
    return 0


def _resume_latest(
    bead_id: str,
    *,
    hub: Path,
    beads: beads_mod.BeadsLike,
    config: config_mod.Config,
    harness_name: str,
) -> int:
    """Finalize a ``native_completed``/``validated``/``invalid`` attempt (SPEC §8.4).

    A stored step 8 status is kept; only an attempt without one is
    classified again. Validation reads the captured ``attempt-<n>``
    report, never the worktree path (SPEC §6.2 step 5).
    """
    numbers = attempt_mod.existing_attempts(hub / config.project.runs / bead_id)
    if not numbers:
        return 2
    n = numbers[-1]
    attempt_dir = attempt_mod.attempt_dir(hub, config.project.runs, bead_id, n)
    state = attempt_mod.read_state(attempt_dir)
    attempt_id = str(state.get("attempt_id") or f"{bead_id}#{n}")
    attempt_obj = attempt_mod.Attempt(bead=bead_id, n=n, attempt_id=attempt_id, dir=attempt_dir)
    bead = beads.show(bead_id)
    stored = _stored_execution(attempt_dir)
    info = worktree_mod.prepare(
        hub=hub,
        bead=bead_id,
        worktrees=config.project.worktrees,
        link_into_worktrees=config.project.link_into_worktrees,
        again=False,
    )
    input_path = attempt_dir / "input.json"
    try:
        saved = jsonio.loads(input_path.read_text())
        hashes = dict(saved.get("input_hashes") or {})
        base_commit = str(saved.get("base_commit") or info.base_commit)
    except (OSError, json.JSONDecodeError, ValueError, AttributeError):
        hashes = {}
        base_commit = info.base_commit
    legacy_path = attempt_mod.worktree_report_path(info.path, n)
    capture = _capture_resume(attempt_dir, legacy_path, stored)
    session_id = state.get("session_id")
    if session_id is None:
        try:
            session_id = jsonio.loads((attempt_dir / "envelope.json").read_text()).get(
                "session_id"
            )
        except (OSError, json.JSONDecodeError, ValueError, AttributeError):
            session_id = None
    finish_args = _FinishArgs(
        hub=hub,
        config=config,
        beads=beads,
        bead=bead,
        harness_name=harness_name,
        attempt=attempt_obj,
        worktree_path=info.path,
        base_commit=base_commit,
        input_hashes=hashes,
        started_at=str(state.get("updated") or attempt_mod.utc_now()),
        notes=["resumed without a new attempt"],
    )
    # An attempt never classified (no stored execution_status) is classified
    # again here; the stop-requested marker counts then too (SPEC §4.4 point 2).
    stopped = stored is None and (attempt_dir / "stop-requested").exists()
    return _finish_attempt(
        finish_args,
        capture=capture,
        native_session=session_id,
        native_error=None,
        proc_exit=None if stored is envelope_mod.ExecutionStatus.LAUNCH_FAILED else 0,
        interrupted=stored is envelope_mod.ExecutionStatus.INTERRUPTED or stopped,
        timed_out=stored is envelope_mod.ExecutionStatus.TIMED_OUT,
        launch_failed=stored is envelope_mod.ExecutionStatus.LAUNCH_FAILED,
        transition_from=None,
    )


def _envelope_execution_status(attempt_dir: Path) -> str:
    """The ``execution_status`` just written to ``envelope.json``, or ``""``."""
    try:
        data = jsonio.loads((attempt_dir / "envelope.json").read_text())
        value = data.get("execution_status")
        return value if isinstance(value, str) else ""
    except (OSError, json.JSONDecodeError, ValueError):
        return ""


def run_turn(
    bead_id: str,
    *,
    hub: Path,
    beads: beads_mod.BeadsLike,
    config: config_mod.Config,
    bead: beads_mod.Bead,
    harness_name: str,
    worktree_path: Path,
    resume_session: str,
    text: str,
    msg_id: str | None,
    resumed_from: str,
    timeout_s: int | None = None,
) -> tuple[int, str]:
    """Run one resumed turn: SPEC §7.1 steps 5-11 with a plain-text prompt (SPEC §9.2).

    Used only by ``helios resume``: the prompt is ``<text>`` plus the report
    path, not the full §7.2 assembly, and the harness is asked to continue
    ``resume_session`` instead of starting fresh. Returns the SPEC §7.1 exit
    code and the execution status just recorded, so the caller can decide
    whether to ack a delivered message.
    """
    if not _RUN_ACTIVE.is_set():
        _INTERRUPT.clear()
    from helios.harness import get as harness_get

    harness = harness_get(harness_name)
    harness_cfg = config.harness.get(harness_name)
    effective_timeout = (
        timeout_s if timeout_s is not None else (harness_cfg.timeout_s if harness_cfg else 3600)
    )
    # SPEC §9.2: the interrupt is checked immediately before an attempt is
    # allocated, so a SIGINT landing after the caller's own pre-turn check
    # (still between it and this allocation) still allocates nothing.
    if _INTERRUPT.is_set():
        return 4, envelope_mod.ExecutionStatus.INTERRUPTED.value
    attempt_obj, alloc_notes = attempt_mod.allocate(
        hub=hub, runs_rel=config.project.runs, bead=bead_id, worktree=worktree_path
    )
    report_path = attempt_mod.worktree_report_path(worktree_path, attempt_obj.n)
    prompt_text = f"{text}\n\nReport path: {report_path}\n"
    (attempt_obj.dir / "prompt.md").write_text(prompt_text)
    (attempt_obj.dir / "report-schema.json").write_text(
        envelope_mod.schema_text("agent-report")
    )
    base_commit = _head_commit(worktree_path)
    hashes = {"prompt": sha256_text(prompt_text)}
    input_payload: dict[str, object] = {
        "harness": harness_name,
        "input_hashes": hashes,
        "base_commit": base_commit,
        "bead": bead_id,
        "resumed_from": resumed_from,
    }
    if msg_id is not None:
        input_payload["msg_id"] = msg_id
    _write_input_json(attempt_obj.dir / "input.json", input_payload)
    raw_dir = attempt_obj.dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    spec = LaunchSpec(
        bead=bead_id,
        attempt=attempt_obj.n,
        worktree=worktree_path,
        prompt=prompt_text,
        report_path=report_path,
        report_schema_path=attempt_obj.dir / "report-schema.json",
        raw_dir=raw_dir,
        model=harness_cfg.model if harness_cfg else None,
        effort=harness_cfg.effort if harness_cfg else None,
        timeout_s=effective_timeout,
        resume_session=resume_session,
        server_url=harness_cfg.server_url if harness_cfg else None,
        extra_args=tuple(harness_cfg.extra_args) if harness_cfg else (),
        env=dict(os.environ),
    )
    argv = harness.argv(spec)
    stdin_text = harness.stdin_text(spec)
    stdout_path = attempt_obj.dir / "stdout.jsonl"
    stderr_path = attempt_obj.dir / "stderr.log"
    started_at = attempt_mod.utc_now()
    env = dict(os.environ)
    env.update(
        {
            "HELIOS_BEAD": bead_id,
            "HELIOS_ATTEMPT": attempt_obj.attempt_id,
            "HELIOS_HARNESS": harness_name,
            "HELIOS_HUB": str(hub),
            "HELIOS_REPORT": str(report_path),
        }
    )
    run_key = str(attempt_obj.dir)
    launched_box = {"fired": False}

    def _record_launched(pid: int) -> None:
        launched_box["fired"] = True
        attempt_mod.transition(attempt_obj.dir, "launched", pid=pid)
        try:
            pid_start = attempt_mod.read_pid_start(pid)
        except Exception:
            pid_start = None
        attempt_mod.transition(attempt_obj.dir, "launched", pid=pid, pid_start=pid_start)
        events_mod.append(
            hub,
            source="helios",
            type="launched",
            bead=bead_id,
            attempt=attempt_obj.attempt_id,
            session=None,
            detail=f"harness {harness_name}",
        )

    stop_path = attempt_obj.dir / "stop-requested"
    proc_exit, timed_out, interrupted, launch_failed, _child = _launch_and_wait(
        argv,
        cwd=worktree_path,
        env=env,
        stdin_text=stdin_text,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        timeout_s=effective_timeout,
        run_key=run_key,
        on_launched=_record_launched,
        stop_path=stop_path,
    )
    if not launch_failed and stop_path.exists():
        interrupted = True
    native = harness.parse(spec, proc_exit, stdout_path)
    if launch_failed:
        attempt_mod.transition(
            attempt_obj.dir,
            "launch_failed",
            execution_status=envelope_mod.ExecutionStatus.LAUNCH_FAILED.value,
            session_id=native.session_id,
        )
    finish_args = _FinishArgs(
        hub=hub,
        config=config,
        beads=beads,
        bead=bead,
        harness_name=harness_name,
        attempt=attempt_obj,
        worktree_path=worktree_path,
        base_commit=base_commit,
        input_hashes=hashes,
        started_at=started_at,
        notes=list(alloc_notes),
        steered=[msg_id] if msg_id is not None else [],
    )
    exit_code = _finish_attempt(
        finish_args,
        capture=_capture_fresh(native.structured, report_path),
        native_session=native.session_id,
        native_error=native.native_error,
        proc_exit=proc_exit,
        interrupted=interrupted,
        timed_out=timed_out,
        launch_failed=launch_failed,
        transition_from=None if launch_failed else "launched",
        launched=launched_box["fired"],
    )
    return exit_code, _envelope_execution_status(attempt_obj.dir)


def preflight_errors(
    bead_ids: list[str],
    *,
    hub: Path,
    beads: beads_mod.BeadsLike,
    config: config_mod.Config,
    memory_has: Callable[[str], bool] | None = None,
) -> list[str]:
    """Preflight (SPEC §7.1 step 2) for a batch of beads; empty means go.

    Raises :class:`MemoryLookupError` on a backend failure rather than a
    missing key (SPEC §7.1, §13); the caller prints it as is, with no
    ``preflight:`` prefix (SPEC §7.1, round 7).
    """
    from helios import preflight as preflight_mod

    hub = hub.resolve()
    loaded = [beads.show(bid) for bid in bead_ids]
    ctx = preflight_mod.PreflightContext(
        hub=hub,
        memory_has=memory_has if memory_has is not None else (lambda key: False),
        stages=config.stages,
        runs_rel=config.project.runs,
        units_dir=config.project.units,
        worktrees_rel=config.project.worktrees,
        bead_show=beads.show,
    )
    errors = preflight_mod.check(loaded, ctx)
    for bead in loaded:
        missing = _missing_skill(hub, bead.kind)
        if missing is not None:
            errors.append(f"{bead.id}: {missing}")
    return errors


def run_many(
    bead_ids: list[str],
    *,
    hub: Path,
    beads: beads_mod.BeadsLike,
    config: config_mod.Config | None = None,
    harness_override: str | None = None,
    timeout_s: int | None = None,
    again: bool = False,
    dry_run: bool = False,
    max_parallel: int = 3,
    memory_has: Callable[[str], bool] | None = None,
) -> int:
    """Run beads, up to ``max_parallel`` at once; preflight first (SPEC §7.1)."""
    depth = _run_depth_enter()
    outermost = depth == 1
    stopper: threading.Thread | None = None
    if outermost:
        _INTERRUPT.clear()
        with _SEQ_LOCK:
            _SEQ_THREADS.clear()
            _SEQ_DONE.clear()
        if _install_handler():
            _RUN_ACTIVE.set()
            stopper = threading.Thread(
                target=_stopper_main, name="_stopper_main", daemon=True
            )
            stopper.start()
    try:
        hub = hub.resolve()
        cfg = config if config is not None else config_mod.load(hub)
        try:
            errors = preflight_errors(
                bead_ids, hub=hub, beads=beads, config=cfg, memory_has=memory_has
            )
        except MemoryLookupError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if errors:
            for line in errors:
                print(f"preflight: {line}", file=sys.stderr)
            return 2
        if dry_run:
            code = 0
            for bid in bead_ids:
                rc = run_one(
                    bid,
                    hub=hub,
                    beads=beads,
                    config=cfg,
                    harness_override=harness_override,
                    timeout_s=timeout_s,
                    again=again,
                    dry_run=True,
                )
                code = max(code, rc)
            if _INTERRUPT.is_set():
                return 4
            return code
        if len(bead_ids) <= 1 or max_parallel <= 1:
            code = 0
            for bid in bead_ids:
                rc = run_one(
                    bid,
                    hub=hub,
                    beads=beads,
                    config=cfg,
                    harness_override=harness_override,
                    timeout_s=timeout_s,
                    again=again,
                )
                code = max(code, rc)
            if _INTERRUPT.is_set():
                return 4
            return code
        results: dict[str, int] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as pool:
            future_of = {
                pool.submit(
                    run_one,
                    bid,
                    hub=hub,
                    beads=beads,
                    config=cfg,
                    harness_override=harness_override,
                    timeout_s=timeout_s,
                    again=again,
                ): bid
                for bid in bead_ids
            }
            for future in concurrent.futures.as_completed(future_of):
                try:
                    results[future_of[future]] = future.result()
                except Exception:
                    results[future_of[future]] = 4
        if _INTERRUPT.is_set():
            return 4
        return max(results.values(), default=0)
    finally:
        try:
            if outermost:
                _RUN_ACTIVE.clear()
                if stopper is not None:
                    # Never call set() on the main thread while our handler is
                    # installed: a SIGINT landing inside it would re-enter
                    # ``_handle_sigint`` -> ``set()`` on the same non-reentrant
                    # Event lock and deadlock. A helper thread does it instead.
                    waker = threading.Thread(
                        target=_INTERRUPT.set, name="_interrupt_waker", daemon=True
                    )
                    waker.start()
                    waker.join()
                    stopper.join()
                for thread in _seq_threads():
                    thread.join()
        finally:
            if outermost:
                _restore_handler()
            _run_depth_exit()


def run_one_in_window(
    bead_id: str,
    *,
    hub: Path,
    beads: beads_mod.Beads | beads_mod.FakeBeads,
    config: config_mod.Config | None = None,
    harness_override: str | None = None,
    timeout_s: int | None = None,
    again: bool = False,
) -> int:
    """``helios run --in-window`` (SPEC §9.1).

    Runs preflight itself, exactly as plain ``helios run`` does, before any
    attempt, worktree or status change (round-1-fix item 1): ``--tmux``'s
    outer preflight only gates opening the windows, and each spawned
    ``helios run <bead> --in-window`` is its own process. Then runs the bead
    through the normal pipeline, tee-ing the child's stdout to this
    process's own stdout, and treats SIGHUP and SIGTERM exactly like SIGINT
    (both delivered to ``_handle_sigint``, since closing a tmux window sends
    SIGHUP), restoring whatever handlers were there before on the way out
    (round-1-fix item 7).
    """
    cfg = config if config is not None else config_mod.load(hub)
    from helios.commands.run import memory_has_for

    try:
        errors = preflight_errors(
            [bead_id], hub=hub, beads=beads, config=cfg, memory_has=memory_has_for(beads, cfg)
        )
    except MemoryLookupError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if errors:
        for line in errors:
            print(f"preflight: {line}", file=sys.stderr)
        return 2

    depth = _run_depth_enter()
    outermost = depth == 1
    stopper: threading.Thread | None = None
    prev_signals: dict[int, object] = {}
    try:
        if outermost:
            _INTERRUPT.clear()
            with _SEQ_LOCK:
                _SEQ_THREADS.clear()
                _SEQ_DONE.clear()
            if _install_handler():
                _RUN_ACTIVE.set()
                for sig in (signal.SIGHUP, signal.SIGTERM):
                    try:
                        prev_signals[sig] = signal.signal(sig, _handle_sigint)
                    except (ValueError, OSError):
                        pass
                stopper = threading.Thread(
                    target=_stopper_main, name="_stopper_main", daemon=True
                )
                stopper.start()
        return run_one(
            bead_id,
            hub=hub,
            beads=beads,
            config=cfg,
            harness_override=harness_override,
            timeout_s=timeout_s,
            again=again,
            tee_stdout=True,
        )
    finally:
        try:
            if outermost:
                _RUN_ACTIVE.clear()
                if stopper is not None:
                    waker = threading.Thread(
                        target=_INTERRUPT.set, name="_interrupt_waker", daemon=True
                    )
                    waker.start()
                    waker.join()
                    stopper.join()
                for thread in _seq_threads():
                    thread.join()
        finally:
            if outermost:
                # A run this process actually interrupted keeps the
                # absorbing handler on SIGHUP/SIGTERM too: a trailing
                # signal from the same storm must never fall through to
                # whatever ran before helios all the way to process exit
                # (round-1-fix item 7 continued; see _restore_handler).
                if not _SIGINT_SETTING:
                    for sig, prev in prev_signals.items():
                        try:
                            signal.signal(sig, prev)  # type: ignore[arg-type]
                        except (ValueError, OSError):
                            pass
                _restore_handler(keep_if_interrupted=True)
            _run_depth_exit()
