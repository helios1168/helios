"""The ``helios run`` pipeline (SPEC §7.1) with report capture (SPEC §6.2).

Library code lives here; argument parsing lives in ``commands/run.py``.
Several beads run in parallel up to ``max_parallel`` (default 3).
``--tmux`` and ``--in-window`` belong to a later bead and are refused here.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

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
_INTERRUPTED: set[str] = set()


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


def capture_report(
    native_structured: dict | None, report_path: Path
) -> tuple[envelope_mod.AgentReport | None, str | None, list[str]]:
    """Capture the report, preferring the native channel (SPEC §6.2).

    Returns (report, report_error, notes). A missing file and no structured
    result gives (None, None, []); the caller maps that to missing_output.
    A present but unparsable or schema-invalid candidate gives
    (None, error, notes) for invalid_output.
    """
    notes: list[str] = []
    file_candidate: dict | None = None
    file_error: str | None = None
    if report_path.is_file():
        try:
            raw = report_path.read_text()
        except OSError as exc:
            file_error = f"cannot read report: {exc}"
            raw = ""
        if file_error is None:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                file_error = f"report is not valid JSON: {exc}"
            else:
                if isinstance(parsed, dict):
                    file_candidate = parsed
                else:
                    file_error = "report JSON is not an object"
    candidate: dict | None = None
    if native_structured is not None:
        candidate = native_structured
        if file_candidate is not None and file_candidate != native_structured:
            notes.append("native result differs from report file; using native")
        elif file_error is not None:
            notes.append(f"report file unreadable ({file_error}); using native")
    else:
        if file_candidate is not None:
            candidate = file_candidate
        elif file_error is not None:
            return None, file_error, notes
        else:
            return None, None, notes
    assert candidate is not None
    try:
        report = envelope_mod.AgentReport.model_validate(candidate)
    except Exception as exc:
        return None, f"report fails schema: {exc}", notes
    return report, None, notes


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


def _signal_group(proc: subprocess.Popen[str], sig: int) -> None:
    """Send a signal to the child's process group, else the child (SPEC §7.1)."""
    try:
        os.killpg(os.getpgid(proc.pid), sig)
        return
    except (ProcessLookupError, PermissionError, ValueError, OSError):
        pass
    try:
        proc.send_signal(sig)
    except (ProcessLookupError, ValueError, OSError):
        pass


def _kill_sequence(proc: subprocess.Popen[str]) -> int | None:
    """SIGINT, wait 10 s, then SIGTERM, wait 5 s, then SIGKILL (SPEC §7.1)."""
    _signal_group(proc, signal.SIGINT)
    try:
        return proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    _signal_group(proc, signal.SIGTERM)
    try:
        return proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    _signal_group(proc, signal.SIGKILL)
    try:
        return proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return proc.poll()


def stop_all_running() -> None:
    """Run the stop sequence on every running attempt (SPEC §7.1 SIGINT)."""
    with _RUNNING_LOCK:
        items = list(_RUNNING.items())
    for key, proc in items:
        _INTERRUPTED.add(key)
        _kill_sequence(proc)


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
    (SPEC §8.2). A SIGINT to the main thread while worker threads run is
    delivered through the shared registry (see ``stop_all_running``).
    """
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
        stdin_arg: int | None = None
        stdin_value: str | None = None
        if stdin_text is not None:
            stdin_arg = subprocess.PIPE
            stdin_value = stdin_text
        else:
            stdin_arg = subprocess.DEVNULL
        try:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                stdout=out_fh,
                stderr=err_fh,
                stdin=stdin_arg,
                env=env,
                text=True,
                start_new_session=True,
            )
        except OSError:
            return None, False, False, True, None
        child_pid: int | None = proc.pid
        if stdin_value is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin_value)
                proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass
        if on_launched is not None:
            on_launched(child_pid)
        with _RUNNING_LOCK:
            _RUNNING[run_key] = proc
        try:
            try:
                exit_code = proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                exit_code = _kill_sequence(proc)
                return exit_code, True, False, False, child_pid
            except KeyboardInterrupt:
                exit_code = _kill_sequence(proc)
                return exit_code, False, True, False, child_pid
            if run_key in _INTERRUPTED:
                return exit_code, False, True, False, child_pid
            return exit_code, False, False, False, child_pid
        finally:
            with _RUNNING_LOCK:
                _RUNNING.pop(run_key, None)
            _INTERRUPTED.discard(run_key)


def _run_shell(
    command: str, cwd: Path, log_path: Path
) -> tuple[bool, int, str]:
    """Run a configured shell command via ``bash -c``; log output."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["bash", "-c", command],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        log_path.write_text(f"failed to start: {exc}\n")
        return False, 127, f"failed to start: {exc}"
    log_path.write_text(f"$ {command}\n(exit {proc.returncode})\n{proc.stdout}{proc.stderr}")
    detail = (proc.stdout + proc.stderr).strip()[-2000:]
    return proc.returncode == 0, proc.returncode, detail


def _git(worktree: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=worktree, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _head_commit(worktree: Path) -> str:
    return _git(worktree, "rev-parse", "HEAD")


def _stage_and_commit(
    worktree: Path, bead_id: str, summary: str, paths: list[str]
) -> tuple[bool, str]:
    """Stage exactly ``paths`` and commit when anything is staged (SPEC §7.1).

    Returns (committed, head): ``committed`` is True when this call created
    a commit; ``head`` is the worktree HEAD afterwards.
    """
    if paths:
        _git(worktree, "add", "-A", "--", *paths)
    staged = _git(worktree, "diff", "--cached", "--name-only")
    if not staged.strip():
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
    assert report is not None
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


def _finish_attempt(
    args: _FinishArgs,
    *,
    native_session: str | None,
    native_structured: dict | None,
    native_error: str | None,
    proc_exit: int | None,
    interrupted: bool,
    timed_out: bool,
    launch_failed: bool,
    transition_from: str | None,
) -> int:
    """Capture, check, commit, envelop, write back and finalize one attempt."""
    bead = args.bead
    attempt_dir = args.attempt.dir
    n = args.attempt.n
    attempt_id = args.attempt.attempt_id
    report_path = attempt_mod.worktree_report_path(args.worktree_path, n)

    report, report_error, capture_notes = capture_report(native_structured, report_path)
    notes = [*args.notes, *capture_notes]
    has_candidate = report is not None or report_error is not None
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
        attempt_mod.transition(attempt_dir, terminal)
    if report is not None:
        (attempt_dir / "report.json").write_text(
            report.model_dump_json(indent=2, exclude_none=False) + "\n"
        )
    elif has_candidate and report_path.is_file():
        try:
            (attempt_dir / "report.json").write_bytes(report_path.read_bytes())
        except OSError:
            pass

    checks_dir = attempt_dir / "checks"
    checks: list[envelope_mod.Check] = []
    if bead.test:
        passed, code, detail = _run_shell(bead.test, args.worktree_path, checks_dir / "test.log")
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
            typecheck_cmd, args.worktree_path, checks_dir / "typecheck.log"
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
    checks.append(
        envelope_mod.Check(
            name="ownership",
            passed=ownership.passed,
            detail=ownership.detail,
        )
    )
    checks_passed = all(c.passed for c in checks)

    if not ownership.passed:
        output_commit: str | None = None
    else:
        summary = report.summary if report is not None else execution.value
        try:
            _stage_and_commit(
                args.worktree_path, bead.id, summary, list(ownership.allowed)
            )
            output_commit = _head_commit(args.worktree_path)
        except RuntimeError:
            checks.append(
                envelope_mod.Check(
                    name="commit", passed=False, detail="git commit failed"
                )
            )
            checks_passed = False
            output_commit = None

    harness_cfg = args.config.harness.get(args.harness_name)
    verdict: str | None = None
    if report is not None and bead.kind.startswith("verify"):
        overall = envelope_mod.overall_verdict(list(report.findings))
        verdict = overall.value if overall is not None else None
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
    attempt_mod.transition(attempt_dir, "validated" if report is not None else "invalid")

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
    beads_mod.apply_writeback(args.beads, bead.id, plan)
    if not plan.close:
        want = _correct_run_state(
            execution_status=execution, report=report, checks_passed=checks_passed
        )
        if plan.run_state != want:
            args.beads.set_state(bead.id, "run", want, plan.close_reason)
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
    adapter = harness
    latest = attempt_mod.latest_state(hub, config.project.runs, bead_id)
    if latest is None:
        action = "new"
    else:
        action = attempt_mod.classify_recovery(
            latest.get("state"),
            pid_alive=attempt_mod.is_pid_alive(latest.get("pid")),
        )
    print(f"recovery: {action}")
    worktree_path = hub / config.project.worktrees / bead_id
    branch = worktree_mod.branch_name(bead_id)
    numbers = attempt_mod.existing_attempts(hub / config.project.runs / bead_id)
    next_n = (numbers[-1] if numbers else 0) + 1
    attempt_path = hub / config.project.runs / bead_id / f"attempt-{next_n}"
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
    argv = adapter.argv(spec)
    print(f"harness: {harness_name}")
    print(f"argv: {' '.join(argv)}")
    print(f"worktree: {worktree_path}")
    print(f"attempt: {attempt_path}")
    print(f"prompt_bytes: {len(prompt.encode('utf-8'))}")
    return 0


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
    hub = hub.resolve()
    cfg = config if config is not None else config_mod.load(hub)
    bead = beads.show(bead_id)
    skill_error = _missing_skill(hub, bead.kind)
    if skill_error is not None:
        print(f"preflight: {bead_id}: {skill_error}", file=sys.stderr)
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
            print(
                f"attempt {latest.get('attempt_id')} is still running; "
                f"use `helios attach {bead_id}` or `helios stop {bead_id}`"
            )
            return 2
        if action == "resume":
            return _resume_latest(
                bead_id, hub=hub, beads=beads, config=cfg, harness_name=harness_name
            )
        if action in ("finalize_and_new", "crash_and_new"):
            old_dir = attempt_mod.attempt_dir(
                hub, cfg.project.runs, bead_id, _attempt_n(latest.get("attempt_id"), bead_id)
            )
            if old_dir.is_dir():
                try:
                    if action == "crash_and_new":
                        attempt_mod.transition(old_dir, "crashed")
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

    def _record_launched(pid: int) -> None:
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
    if launch_failed:
        attempt_mod.transition(attempt_obj.dir, "launch_failed")
    native = harness.parse(spec, proc_exit, stdout_path)
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
        native_session=native.session_id,
        native_structured=native.structured,
        native_error=native.native_error,
        proc_exit=proc_exit,
        interrupted=interrupted,
        timed_out=timed_out,
        launch_failed=launch_failed,
        transition_from=None if launch_failed else "launched",
    )


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
    """Finalize a ``native_completed``/``validated``/``invalid`` attempt (SPEC §8.4)."""
    from helios.harness import get as harness_get

    harness = harness_get(harness_name)
    numbers = attempt_mod.existing_attempts(hub / config.project.runs / bead_id)
    if not numbers:
        return 2
    n = numbers[-1]
    attempt_dir = attempt_mod.attempt_dir(hub, config.project.runs, bead_id, n)
    state = attempt_mod.read_state(attempt_dir)
    attempt_id = str(state.get("attempt_id") or f"{bead_id}#{n}")
    attempt_obj = attempt_mod.Attempt(bead=bead_id, n=n, attempt_id=attempt_id, dir=attempt_dir)
    bead = beads.show(bead_id)
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
    stdout_path = attempt_dir / "stdout.jsonl"
    harness_cfg = config.harness.get(harness_name)
    report_path = attempt_mod.worktree_report_path(info.path, n)
    spec = LaunchSpec(
        bead=bead_id,
        attempt=n,
        worktree=info.path,
        prompt="",
        report_path=report_path,
        report_schema_path=attempt_dir / "report-schema.json",
        raw_dir=attempt_dir / "raw",
        model=harness_cfg.model if harness_cfg else None,
        effort=harness_cfg.effort if harness_cfg else None,
        timeout_s=harness_cfg.timeout_s if harness_cfg else 3600,
        server_url=harness_cfg.server_url if harness_cfg else None,
        extra_args=tuple(harness_cfg.extra_args) if harness_cfg else (),
        env=dict(os.environ),
    )
    exit_hint: int | None = 0
    try:
        if stdout_path.is_file():
            exit_hint = 0
    except OSError:
        exit_hint = 0
    native = harness.parse(spec, exit_hint, stdout_path)
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
        native_session=native.session_id if native.session_id else state.get("session_id"),
        native_structured=native.structured,
        native_error=None,
        proc_exit=0,
        interrupted=False,
        timed_out=False,
        launch_failed=False,
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
        try:
            for future in concurrent.futures.as_completed(future_of):
                results[future_of[future]] = future.result()
        except KeyboardInterrupt:
            stop_all_running()
            for future in concurrent.futures.as_completed(future_of):
                try:
                    results[future_of[future]] = future.result()
                except KeyboardInterrupt:
                    results[future_of[future]] = 4
                except Exception:
                    results[future_of[future]] = 4
    return max(results.values(), default=0)
