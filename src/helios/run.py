"""The ``helios run`` pipeline (SPEC §7.1) with report capture (SPEC §6.2).

Library code lives here; argument parsing lives in ``commands/run.py``.
Several beads run in parallel up to ``max_parallel`` (default 3).
``--tmux`` and ``--in-window`` belong to a later bead and are refused here.
"""

from __future__ import annotations

import concurrent.futures
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

from helios import attempt as attempt_mod
from helios import beads as beads_mod
from helios import config as config_mod
from helios import envelope as envelope_mod
from helios import events as events_mod
from helios import ownership as ownership_mod
from helios import prompt as prompt_mod
from helios import worktree as worktree_mod
from helios.harness.base import Harness, LaunchSpec

_RUNNING: dict[str, subprocess.Popen[str]] = {}
_RUNNING_LOCK = threading.Lock()
_INTERRUPT = threading.Event()
_RUN_ACTIVE = threading.Event()


def _reg_add(key: str, proc: subprocess.Popen[str]) -> None:
    """Register a running child; always through ``_RUNNING_LOCK``."""
    with _RUNNING_LOCK:
        _RUNNING[key] = proc


def _reg_remove(key: str) -> None:
    """Drop a finished child from the registry."""
    with _RUNNING_LOCK:
        _RUNNING.pop(key, None)


def _reg_snapshot() -> list[subprocess.Popen[str]]:
    """The currently running children."""
    with _RUNNING_LOCK:
        return list(_RUNNING.values())
_RUN_DEPTH = 0
_RUN_DEPTH_LOCK = threading.Lock()
_PREV_SIGINT: Callable[[int, FrameType | None], object] | int | None = None


def _handle_sigint(signum: int, frame: FrameType | None) -> None:
    """Record a SIGINT and return immediately (SPEC §7.1 Interrupts).

    The handler never blocks and takes no lock: a stopper thread started
    by the run waits on the flag and runs the stop sequences. Later
    SIGINTs are ignored.
    """
    _INTERRUPT.set()


def _stopper_main() -> None:
    """Stop every running child group while the run is active (SPEC §7.1).

    Blocks in ``Event.wait()``, then runs the stop sequences of all
    running groups concurrently so each gets its SIGINT at once. Groups
    registered after the flag (a check that raced it) are picked up on
    the next sweep; the thread exits only when the run ends with no
    groups left.
    """
    while True:
        flagged = _INTERRUPT.wait(timeout=0.5)
        procs = _reg_snapshot()
        active = _RUN_ACTIVE.is_set()
        if not active and not procs:
            return
        if not flagged or not procs:
            continue
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(procs), thread_name_prefix="stopper-seq"
        ) as pool:
            list(pool.map(lambda p: _stop_sequence(p, p.pid), procs))


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


def _restore_handler() -> None:
    """Restore the previous SIGINT handler."""
    global _PREV_SIGINT
    prev, _PREV_SIGINT = _PREV_SIGINT, None
    if prev is not None:
        try:
            signal.signal(signal.SIGINT, prev)  # type: ignore[arg-type]
        except ValueError:
            pass


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
) -> tuple[int | None, bool, bool, bool, int | None]:
    """Run one attempt; return (exit, timed_out, interrupted, launch_failed, pid).

    The child runs in its own session and every signal goes to its process
    group (SPEC §7.1 step 7). ``launched`` with the child pid is recorded
    through ``on_launched`` as soon as ``Popen`` returns, before waiting
    (SPEC §8.2). A set interrupt flag skips the launch and records
    ``interrupted`` without starting a child.
    """
    if _INTERRUPT.is_set():
        return None, False, True, False, None
    try:
        out_fh = open(stdout_path, "w")
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
                stdout=out_fh,
                stderr=err_fh,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                env=env,
                text=True,
                start_new_session=True,
            )
        except OSError:
            return None, False, False, True, None
        child_pid: int | None = proc.pid
        pgid = child_pid
        if stdin_text is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin_text)
                proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass
        if on_launched is not None:
            on_launched(child_pid)
        _reg_add(run_key, proc)
        try:
            start = time.monotonic()
            while True:
                if _INTERRUPT.is_set():
                    exit_code = proc.wait()
                    _wait_group_gone(proc, pgid, 30)
                    return exit_code, False, True, False, child_pid
                try:
                    exit_code = proc.wait(timeout=0.05)
                except subprocess.TimeoutExpired:
                    pass
                else:
                    if _INTERRUPT.is_set():
                        _wait_group_gone(proc, pgid, 30)
                        return exit_code, False, True, False, child_pid
                    return exit_code, False, False, False, child_pid
                if time.monotonic() - start >= timeout_s:
                    exit_code = _stop_sequence(proc, pgid)
                    return exit_code, True, False, False, child_pid
        except KeyboardInterrupt:
            exit_code = _stop_sequence(proc, pgid)
            return exit_code, False, True, False, child_pid
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
    kind: str,
    verify_artifacts: str,
    unit: str | None,
    confidential: tuple[str, ...],
    memory_export_dir: str,
) -> tuple[list[str], list[str]]:
    """Sort cached-only paths into allowed and rejected (SPEC §7.4)."""
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
        if kind.startswith("verify"):
            scope = f"{verify_artifacts.rstrip('/')}/{unit}/" if unit else None
            if scope is not None and _is_prefix_match(path, scope):
                allowed.append(path)
            else:
                rejected.append(path)
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
    kind: str,
    execution_status: envelope_mod.ExecutionStatus,
    report: envelope_mod.AgentReport | None,
    checks_passed: bool,
) -> int:
    """Exit codes of SPEC §7.1: 0 done/verified, 3 waiting, 4 failure, 5 check."""
    if execution_status is not envelope_mod.ExecutionStatus.COMPLETED:
        return 4
    if not checks_passed:
        return 5
    if report is None:
        return 4
    if kind.startswith("verify"):
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
        data = json.loads((attempt_dir / "state.json").read_text())
        value = data.get("execution_status")
        if value is not None:
            return envelope_mod.ExecutionStatus(value)
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    try:
        env = json.loads((attempt_dir / "envelope.json").read_text())
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
        parsed = json.loads(raw.decode("utf-8"))
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
            json.dumps(native_structured, ensure_ascii=False, indent=2, sort_keys=True)
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


def _apply_writeback_once(
    beads: beads_mod.BeadsLike, bead_id: str, plan: beads_mod.Writeback, final_state: str
) -> None:
    """Apply a write-back plan with exactly one ``set-state`` call (SPEC §7.5)."""
    from dataclasses import replace

    if not plan.close and plan.run_state != final_state:
        plan = replace(plan, run_state=final_state)
    beads_mod.apply_writeback(beads, bead_id, plan)


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
) -> int:
    """Validate, check, commit, envelop, write back and finalize one attempt."""
    bead = args.bead
    attempt_dir = args.attempt.dir
    n = args.attempt.n
    attempt_id = args.attempt.attempt_id

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

    if transition_from is not None:
        terminal = {
            envelope_mod.ExecutionStatus.INTERRUPTED: "interrupted",
            envelope_mod.ExecutionStatus.TIMED_OUT: "timed_out",
            envelope_mod.ExecutionStatus.LAUNCH_FAILED: "launch_failed",
        }.get(execution, "native_completed" if proc_exit == 0 else "crashed")
        if launch_failed:
            terminal = "launch_failed"
        attempt_mod.transition(
            attempt_dir,
            terminal,
            execution_status=execution.value,
            session_id=native_session,
        )

    checks_dir = attempt_dir / "checks"
    checks: list[envelope_mod.Check] = []
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
                envelope_mod.Check(name="test", passed=False, detail="interrupted")
            )
        if args.config.project.typecheck:
            checks.append(
                envelope_mod.Check(
                    name="typecheck", passed=False, detail="interrupted"
                )
            )
        checks.append(
            envelope_mod.Check(
                name="ownership", passed=False, detail="interrupted"
            )
        )
        checks_passed = False
        output_commit: str | None = None
    else:
        if bead.test:
            passed, code, detail = _run_shell(
                bead.test, args.worktree_path, checks_dir / "test.log",
                f"{attempt_dir}:check:test",
            )
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
        typecheck_cmd = args.config.project.typecheck
        if typecheck_cmd:
            passed, code, detail = _run_shell(
                typecheck_cmd, args.worktree_path, checks_dir / "typecheck.log",
                f"{attempt_dir}:check:typecheck",
            )
            checks.append(
                envelope_mod.Check(
                    name="typecheck",
                    passed=passed,
                    command=typecheck_cmd,
                    exit_code=code,
                    detail=detail,
                    log_path="checks/typecheck.log",
                )
            )
        ownership = ownership_mod.check(
            worktree=args.worktree_path,
            base_commit=args.base_commit,
            files=list(bead.files),
            always_allowed=args.config.project.always_allowed,
            kind=bead.kind,
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
                kind=bead.kind,
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
            _stage_and_commit(
                args.worktree_path, bead.id, summary, list(ownership.allowed)
            )
            output_commit = _head_commit(args.worktree_path)

    harness_cfg = args.config.harness.get(args.harness_name)
    verdict: str | None = None
    if report is not None and bead.kind.startswith("verify"):
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
            steered=[],
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
        bead_kind=bead.kind,
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
        kind=bead.kind,
        execution_status=execution,
        report=report,
        checks_passed=checks_passed,
    )


def _assemble_inputs(
    *,
    hub: Path,
    config: config_mod.Config,
    bead: beads_mod.Bead,
    worktree_path: Path,
    branch: str,
    attempt_id: str,
    report_path: Path,
) -> tuple[str, dict[str, str], str, list[tuple[str, str]], dict[str, str], str | None]:
    """Assemble the prompt and its hashed inputs (SPEC §7.2, §8.3)."""
    skills_dir = hub / "skills"
    agents_path = hub / "AGENTS.md"
    bead_map = {
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
            pid_alive=attempt_mod.is_pid_alive(latest.get("pid")),
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
    prompt, _, _, _, _, _ = _assemble_inputs(
        hub=hub,
        config=config,
        bead=bead,
        worktree_path=worktree_path,
        branch=branch,
        attempt_id=f"{bead_id}#{next_n}",
        report_path=report_path,
    )
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
        saved = json.loads((attempt_dir / "input.json").read_text())
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
                pid_alive=attempt_mod.is_pid_alive(latest.get("pid")),
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
                            attempt_mod.transition(
                                old_dir,
                                "crashed",
                                execution_status=envelope_mod.ExecutionStatus.CRASHED.value,
                            )
                            stored = _stored_execution(old_dir)
                            _write_recovery_envelope(
                                hub=hub, config=cfg, bead=bead,
                                harness_name=harness_name, attempt_dir=old_dir,
                                n=old_n,
                                attempt_id=str(latest.get("attempt_id") or f"{bead_id}#{old_n}"),
                                execution=stored or envelope_mod.ExecutionStatus.CRASHED,
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

        info = worktree_mod.prepare(
            hub=hub,
            bead=bead_id,
            worktrees=cfg.project.worktrees,
            link_into_worktrees=cfg.project.link_into_worktrees,
            again=again,
        )
        if _INTERRUPT.is_set():
            return 4
        worktree_path = info.path
        base_commit = info.base_commit
        attempt_obj, alloc_notes = attempt_mod.allocate(
            hub=hub, runs_rel=cfg.project.runs, bead=bead_id, worktree=worktree_path
        )
        report_path = attempt_mod.worktree_report_path(worktree_path, attempt_obj.n)
        prompt, hashes, _, _, _, _ = _assemble_inputs(
            hub=hub,
            config=cfg,
            bead=bead,
            worktree_path=worktree_path,
            branch=info.branch,
            attempt_id=attempt_obj.attempt_id,
            report_path=report_path,
        )
        (attempt_obj.dir / "prompt.md").write_text(prompt)
        (attempt_obj.dir / "report-schema.json").write_text(
            envelope_mod.schema_text("agent-report")
        )
        (attempt_obj.dir / "input.json").write_text(
            json.dumps(
                {"input_hashes": hashes, "base_commit": base_commit, "bead": bead_id},
                indent=2,
                sort_keys=True,
            )
            + "\n"
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
            events_mod.append(
                hub,
                source="helios",
                type="launched",
                bead=bead_id,
                attempt=attempt_obj.attempt_id,
                session=None,
                detail=f"harness {harness_name}",
            )

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
        )
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
        saved = json.loads(input_path.read_text())
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
            session_id = json.loads((attempt_dir / "envelope.json").read_text()).get(
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
    return _finish_attempt(
        finish_args,
        capture=capture,
        native_session=session_id,
        native_error=None,
        proc_exit=None if stored is envelope_mod.ExecutionStatus.LAUNCH_FAILED else 0,
        interrupted=stored is envelope_mod.ExecutionStatus.INTERRUPTED,
        timed_out=stored is envelope_mod.ExecutionStatus.TIMED_OUT,
        launch_failed=stored is envelope_mod.ExecutionStatus.LAUNCH_FAILED,
        transition_from=None,
    )


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
) -> int:
    """Run beads, up to ``max_parallel`` at once; preflight first (SPEC §7.1)."""
    from helios import preflight as preflight_mod

    depth = _run_depth_enter()
    outermost = depth == 1
    stopper: threading.Thread | None = None
    if outermost:
        _INTERRUPT.clear()
        if _install_handler():
            _RUN_ACTIVE.set()
            stopper = threading.Thread(
                target=_stopper_main, name="_stopper_main", daemon=True
            )
            stopper.start()
    try:
        hub = hub.resolve()
        cfg = config if config is not None else config_mod.load(hub)
        loaded = [beads.show(bid) for bid in bead_ids]
        ctx = preflight_mod.PreflightContext(
            hub=hub,
            memory_has=lambda key: False,
            runs_rel=cfg.project.runs,
            units_dir=cfg.project.units,
        )
        errors = preflight_mod.check(loaded, ctx)
        for bead in loaded:
            missing = _missing_skill(hub, bead.kind)
            if missing is not None:
                errors.append(f"{bead.id}: {missing}")
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
        if outermost:
            _RUN_ACTIVE.clear()
            if stopper is not None:
                stopper.join()
            _restore_handler()
        _run_depth_exit()
