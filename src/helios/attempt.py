"""Attempt lifecycle records (SPEC §8).

``state.json`` is ``{"state", "attempt_id", "pid", "session_id",
"execution_status", "updated"}`` (SPEC §8.2), written to a temp file and
moved with ``os.replace``; every transition appends one line to
``state.log``. Every write carries all six keys, and a transition keeps the
stored ``execution_status`` (and ``pid`` and ``session_id``) unless it sets
a new value. An attempt directory without ``state.json`` reads as state
``allocated`` with null pid, session and status (SPEC §8.3). Recovery
classification (SPEC §8.4) is a pure function; staleness (SPEC §8.5)
compares input hashes and ancestry.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

STATES = (
    "allocated",
    "launched",
    "native_completed",
    "interrupted",
    "timed_out",
    "crashed",
    "launch_failed",
    "validated",
    "invalid",
    "finalized",
)

FINAL_STATES = ("finalized",)

RESUMABLE_STATES = ("native_completed", "validated", "invalid")

FINALIZE_AND_NEW_STATES = ("interrupted", "timed_out", "crashed", "launch_failed")

RecoveryAction = Literal["refuse", "resume", "finalize_and_new", "crash_and_new", "new"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def runs_dir(hub: Path, runs_rel: str, bead: str) -> Path:
    return hub / runs_rel / bead


def attempt_dir(hub: Path, runs_rel: str, bead: str, n: int) -> Path:
    return runs_dir(hub, runs_rel, bead) / f"attempt-{n}"


def existing_attempts(bead_runs: Path) -> list[int]:
    """Sorted attempt numbers already on disk."""
    if not bead_runs.is_dir():
        return []
    out = []
    for child in bead_runs.iterdir():
        match = re.fullmatch(r"attempt-(\d+)", child.name)
        if match and child.is_dir():
            out.append(int(match.group(1)))
    return sorted(out)


def next_attempt_number(bead_runs: Path) -> int:
    """One more than the highest existing attempt directory (SPEC §8.3)."""
    numbers = existing_attempts(bead_runs)
    return (numbers[-1] if numbers else 0) + 1


def worktree_report_path(worktree: Path, n: int) -> Path:
    """Where the agent writes its report inside the worktree (SPEC §8.1)."""
    return worktree / ".helios" / f"attempt-{n}" / "report.json"


@dataclass(frozen=True)
class Attempt:
    bead: str
    n: int
    attempt_id: str
    dir: Path


def read_state(dir: Path) -> dict[str, Any]:
    """Read ``state.json``; a missing file reads as ``allocated`` (SPEC §8.3).

    Reading never raises for a missing file: the record is state
    ``allocated`` with null pid, session id and execution status.
    """
    try:
        return json.loads((dir / "state.json").read_text())
    except FileNotFoundError:
        pass
    match = re.fullmatch(r"attempt-(\d+)", dir.name)
    attempt_id = f"{dir.parent.name}#{match.group(1)}" if match else dir.name
    return {
        "state": "allocated",
        "attempt_id": attempt_id,
        "pid": None,
        "session_id": None,
        "execution_status": None,
        "updated": utc_now(),
    }


def write_state(
    dir: Path,
    *,
    attempt_id: str,
    state: str,
    pid: int | None = None,
    session_id: str | None = None,
    execution_status: str | None = None,
) -> dict[str, Any]:
    """Write ``state.json`` atomically and append one line to ``state.log``."""
    if state not in STATES:
        raise ValueError(f"unknown attempt state {state!r}")
    record = {
        "state": state,
        "attempt_id": attempt_id,
        "pid": pid,
        "session_id": session_id,
        "execution_status": execution_status,
        "updated": utc_now(),
    }
    dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dir), prefix="state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(record, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, dir / "state.json")
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    with open(dir / "state.log", "a") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def allocate(
    *,
    hub: Path,
    runs_rel: str,
    bead: str,
    worktree: Path,
    pid: int | None = None,
) -> tuple[Attempt, list[str]]:
    """Allocate attempt ``n``: move a stale worktree report aside, set ``allocated``.

    Returns the attempt and notes (SPEC §8.3).
    """
    n = next_attempt_number(runs_dir(hub, runs_rel, bead))
    while True:
        dir = attempt_dir(hub, runs_rel, bead, n)
        try:
            dir.mkdir(parents=True)
        except FileExistsError:
            n += 1
            continue
        break
    notes: list[str] = []
    stale = worktree_report_path(worktree, n)
    if stale.is_file():
        target = dir / "stale-report.json"
        target.write_bytes(stale.read_bytes())
        stale.unlink()
        notes.append(f"moved stale report to {target}")
    attempt = Attempt(bead=bead, n=n, attempt_id=f"{bead}#{n}", dir=dir)
    write_state(dir, attempt_id=attempt.attempt_id, state="allocated", pid=pid)
    return attempt, notes


def transition(
    dir: Path,
    state: str,
    *,
    pid: int | None = None,
    session_id: str | None = None,
    execution_status: str | None = None,
) -> dict[str, Any]:
    """Move an attempt to ``state`` (SPEC §8.2).

    The stored ``pid``, ``session_id`` and ``execution_status`` survive
    unless the call sets a new value.
    """
    current = read_state(dir)
    stored = current.get("execution_status")
    return write_state(
        dir,
        attempt_id=current["attempt_id"],
        state=state,
        pid=pid if pid is not None else current.get("pid"),
        session_id=session_id if session_id is not None else current.get("session_id"),
        execution_status=(
            execution_status if execution_status is not None else stored
        ),
    )


def latest_state(hub: Path, runs_rel: str, bead: str) -> dict[str, Any] | None:
    """The state record of the highest attempt, or None when there is none."""
    numbers = existing_attempts(runs_dir(hub, runs_rel, bead))
    if not numbers:
        return None
    return read_state(attempt_dir(hub, runs_rel, bead, numbers[-1]))


def classify_recovery(state: str | None, *, pid_alive: bool) -> RecoveryAction:
    """Recovery decision as a pure function (SPEC §8.4).

    - no previous attempt, or the latest is finalized: ``new``.
    - a live process: ``refuse`` (print the attach and stop commands).
    - ``native_completed``, ``validated`` or ``invalid``: ``resume`` the same
      attempt through validation, checks and write-back, then finalize.
    - ``interrupted``, ``timed_out``, ``crashed`` or ``launch_failed`` with no
      live process: ``finalize_and_new`` (finalize with that state as the
      execution status, then allocate a new attempt).
    - ``allocated`` or ``launched`` with no live process: ``crash_and_new``
      (record ``crashed``, finalize, allocate a new attempt).
    """
    if state is None or state == "finalized":
        return "new"
    if pid_alive:
        return "refuse"
    if state in RESUMABLE_STATES:
        return "resume"
    if state in FINALIZE_AND_NEW_STATES:
        return "finalize_and_new"
    return "crash_and_new"


def is_pid_alive(pid: int | None) -> bool:
    """True when ``pid`` names a live process."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def is_stale(
    envelope: dict[str, Any],
    current_hashes: dict[str, str],
    *,
    repo: Path | None = None,
    impl_commit: str | None = None,
) -> bool:
    """Stale evidence check (SPEC §8.5): hashes differ or base is not an ancestor."""
    if dict(envelope.get("input_hashes") or {}) != dict(current_hashes):
        return True
    if repo is not None and impl_commit is not None:
        base = envelope.get("base_commit")
        if not base or not is_ancestor(repo, str(base), impl_commit):
            return True
    return False
