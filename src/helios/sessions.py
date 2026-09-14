"""Session discovery and control (SPEC §9.2)."""

from __future__ import annotations

import json
import os
import signal
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, cast

from helios import attempt, beads, config, events
from helios.harness.base import LaunchSpec


def _utc_seconds() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _attempt_dirs(runs: Path, bead: str) -> list[Path]:
    return [runs / bead / f"attempt-{n}" for n in attempt.existing_attempts(runs / bead)]


def latest(runs: Path, bead: str) -> Path | None:
    dirs = _attempt_dirs(runs, bead)
    return dirs[-1] if dirs else None


def safe_state(directory: Path) -> dict[str, Any]:
    try:
        state = attempt.read_state(directory)
        if not isinstance(state, dict) or not isinstance(state.get("state"), str):
            raise ValueError("invalid state")
        normalized = dict(state)
        pid = normalized.get("pid")
        normalized["pid"] = (
            pid if isinstance(pid, int) and not isinstance(pid, bool) and 0 < pid < 2**31 else None
        )
        session_id = normalized.get("session_id")
        normalized["session_id"] = session_id if isinstance(session_id, str) else None
        execution_status = normalized.get("execution_status")
        normalized["execution_status"] = execution_status if isinstance(execution_status, str) else None
        normalized["attempt_id"] = normalized["attempt_id"] if isinstance(normalized.get("attempt_id"), str) else None
        normalized["updated"] = normalized["updated"] if isinstance(normalized.get("updated"), str) else None
        return normalized
    except (OSError, ValueError, TypeError, json.JSONDecodeError, RecursionError):
        return {"state": "allocated", "attempt_id": None, "pid": None,
                "session_id": None, "updated": None}


def _input(directory: Path) -> dict[str, Any]:
    try:
        value = json.loads((directory / "input.json").read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError, RecursionError):
        return {}


def _age(updated: Any, now: datetime | None = None) -> str | None:
    if not isinstance(updated, str):
        return None
    try:
        when = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        if when.tzinfo is None:
            return None
        seconds = max(0, int(((now or datetime.now(timezone.utc)) - when).total_seconds()))
    except (TypeError, ValueError):
        return None
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _last_event(hub: Path, bead: str, attempt_id: str | None) -> str | None:
    if not attempt_id:
        return None
    try:
        lines = (hub / events.EVENT_PATH).read_bytes().split(b"\n")
    except OSError:
        return None
    result = None
    for raw_line in lines:
        try:
            line = raw_line.decode("utf-8")
        except UnicodeDecodeError:
            continue
        try:
            value = json.loads(line, parse_constant=_reject_constant)
        except (TypeError, ValueError, json.JSONDecodeError, RecursionError):
            continue
        if not isinstance(value, dict):
            continue
        if value.get("bead") == bead and value.get("attempt") == attempt_id:
            result = value.get("type")
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def rows(hub: Path, runs_rel: str, *, bead_store: beads.BeadsLike | None = None,
         now: datetime | None = None) -> list[dict[str, Any]]:
    """Return `ps` rows, sorted by bead id (SPEC §9.2)."""
    runs = hub / runs_rel
    if not runs.is_dir():
        return []
    out: list[dict[str, Any]] = []
    store = bead_store or beads.Beads(hub)
    worktree_root = hub / config.load(hub).project.worktrees
    for bead_dir in sorted((p for p in runs.iterdir() if p.is_dir()), key=lambda p: p.name):
        directory = latest(runs, bead_dir.name)
        if directory is None:
            continue
        state = safe_state(directory)
        try:
            bead = store.show(bead_dir.name)
            unit, kind, in_progress = bead.unit, bead.kind, bead.status == "in_progress"
        except Exception:
            unit, kind, in_progress = None, None, False
        if state["state"] == "finalized" and not in_progress:
            continue
        inp = _input(directory)
        harness = inp.get("harness") or None
        session_id = state.get("session_id")
        session = f"{harness}:{session_id}" if harness is not None and session_id is not None else None
        pid = state.get("pid")
        alive = attempt.is_pid_alive(pid) if pid is not None else False
        stored = state["state"]
        attempt_id = state.get("attempt_id") or f"{bead_dir.name}#{directory.name.removeprefix('attempt-')}"
        row = {"bead": bead_dir.name, "unit": unit, "kind": kind, "harness": harness,
               "state": stored, "attempt": attempt_id,
               "age": _age(state.get("updated"), now),
               "worktree": str(worktree_root / bead_dir.name) if (worktree_root / bead_dir.name).is_dir() else None,
               "session": session, "alive": alive,
               "last_event": _last_event(hub, bead_dir.name, attempt_id)}
        out.append(row)
    return out


def attach(hub: Path, runs_rel: str, bead: str, *, harness_lookup: Callable[[str], Any],
           execvp: Callable[[str, list[str]], Any] = os.execvp) -> None:
    """Build and exec an adapter attach command (SPEC §9.2)."""
    directory = latest(hub / runs_rel, bead)
    if directory is None:
        raise ValueError(f"no attempt for {bead}")
    inp, state = _input(directory), safe_state(directory)
    project_config = config.load(hub)
    attempt_number = directory.name.removeprefix("attempt-")
    attempt_id = state.get("attempt_id") or f"{bead}#{attempt_number}"
    harness_name = inp.get("harness")
    if not isinstance(harness_name, str) or not harness_name:
        raise ValueError(f"no harness recorded for {attempt_id}")
    worktree = hub / project_config.project.worktrees / bead
    if not worktree.is_dir():
        raise ValueError(f"worktree missing for {attempt_id}")
    session_id = state.get("session_id")
    if session_id is None:
        raise ValueError(f"no session recorded for {attempt_id}")
    if not attempt_number.isdigit():
        raise ValueError(f"invalid attempt directory {directory.name}")
    n = int(attempt_number)
    harness_config = project_config.harness.get(harness_name, config.HarnessConfig())
    spec = LaunchSpec(bead=bead, attempt=n, worktree=worktree, prompt="",
                      report_path=directory / "report.json", report_schema_path=hub / "schemas/agent-report.schema.json",
                      raw_dir=directory / "raw", model=harness_config.model, effort=harness_config.effort,
                      timeout_s=harness_config.timeout_s, server_url=harness_config.server_url,
                      extra_args=harness_config.extra_args)
    harness = harness_lookup(harness_name)
    argv = list(harness.attach_command(str(session_id), spec))
    if not argv:
        raise ValueError(f"empty attach command for {harness_name}")
    binary = harness_config.resolved_binary(harness_name)
    argv[0] = binary
    os.chdir(worktree)
    execvp(argv[0], argv)


def stop(hub: Path, runs_rel: str, bead: str) -> None:
    """Request interruption and signal a live attempt process group (SPEC §9.2)."""
    directory = latest(hub / runs_rel, bead)
    state = safe_state(directory) if directory else {}
    pid = state.get("pid")
    if not directory or state.get("state") != "launched" or not attempt.is_pid_alive(pid):
        raise ValueError(f"no running attempt for {bead}")
    path = directory / "stop-requested"
    fd, temporary = tempfile.mkstemp(dir=directory, prefix="stop.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(_utc_seconds() + "\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    if not isinstance(pid, int):
        raise ValueError(f"no running attempt for {bead}")
    process_id = cast(int, pid)
    try:
        os.killpg(process_id, signal.SIGINT)
    except (ProcessLookupError, PermissionError):
        pass
