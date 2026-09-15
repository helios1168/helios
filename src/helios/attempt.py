"""Attempt lifecycle records (SPEC §8).

``state.json`` is ``{"state", "attempt_id", "pid", "pid_start", "session_id",
"execution_status", "updated"}`` (SPEC §8.2), written to a temp file and
moved with ``os.replace``; every transition appends one line to
``state.log``. Every write carries all seven keys, and a transition keeps the
stored ``execution_status`` (and ``pid``, ``pid_start`` and ``session_id``)
unless it sets a new value. ``pid_start`` is set once at launch (SPEC §7.1
step 7) and never changes after. An attempt directory without ``state.json``
reads as state ``allocated`` with null pid, pid_start, session and status
(SPEC §8.3). Recovery classification (SPEC §8.4) is a pure function;
staleness (SPEC §8.5) compares input hashes and ancestry.

Liveness (SPEC §8.2, §9.2) is one function, ``is_pid_alive``, used by every
caller that asks whether an attempt is live (``helios stop``, ``helios ps``,
preflight, resume, recovery): the leader pid exists and its current
``ps -o lstart=`` text equals ``pid_start``, or, when the leader no longer
exists (or ``pid_start`` is null), the process group is alive
(``os.killpg(pid, 0)`` succeeds or raises ``PermissionError``). A leader
whose start time differs from ``pid_start`` is a reused pid: never live.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from helios import jsonio

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
    """Read ``state.json``; damage reads as ``allocated`` (SPEC §8.3).

    A ``state.json`` that is missing or unreadable (empty, not valid
    JSON, not an object, or without a string ``state``) reads as state
    ``allocated`` with null pid, pid_start, session id and execution
    status; reading never raises for these cases. A file written before
    ``pid_start`` existed has no such key, so ``.get("pid_start")`` reads
    as null the same way.
    """
    try:
        data = jsonio.loads((dir / "state.json").read_text())
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("state"), str):
        match = re.fullmatch(r"attempt-(\d+)", dir.name)
        attempt_id = f"{dir.parent.name}#{match.group(1)}" if match else dir.name
        return {
            "state": "allocated",
            "attempt_id": attempt_id,
            "pid": None,
            "pid_start": None,
            "session_id": None,
            "execution_status": None,
            "updated": utc_now(),
        }
    return data


def write_state(
    dir: Path,
    *,
    attempt_id: str,
    state: str,
    pid: int | None = None,
    pid_start: str | None = None,
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
        "pid_start": pid_start,
        "session_id": session_id,
        "execution_status": execution_status,
        "updated": utc_now(),
    }
    dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dir), prefix="state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(jsonio.dumps(record, indent=2, sort_keys=True))
            fh.write("\n")
        os.replace(tmp, dir / "state.json")
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    with open(dir / "state.log", "a") as fh:
        fh.write(jsonio.dumps(record, sort_keys=True) + "\n")
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
    pid_start: str | None = None,
    session_id: str | None = None,
    execution_status: str | None = None,
) -> dict[str, Any]:
    """Move an attempt to ``state`` (SPEC §8.2).

    The stored ``pid``, ``pid_start``, ``session_id`` and
    ``execution_status`` survive unless the call sets a new value.
    """
    current = read_state(dir)
    stored = current.get("execution_status")
    return write_state(
        dir,
        attempt_id=current["attempt_id"],
        state=state,
        pid=pid if pid is not None else current.get("pid"),
        pid_start=pid_start if pid_start is not None else current.get("pid_start"),
        session_id=session_id if session_id is not None else current.get("session_id"),
        execution_status=(
            execution_status if execution_status is not None else stored
        ),
    )


def _normalize_liveness_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize ``pid`` and ``pid_start`` the way SPEC §8.3 reads them.

    ``pid`` is kept only when it is an int, not a bool, with
    ``0 < pid < 2**31``, else null; ``pid_start`` is kept only when it is a
    str, else null. Every value normalized this way is safe to pass straight
    to ``is_pid_alive`` (mirrors the rule ``sessions.safe_state`` applies).
    """
    pid = record.get("pid")
    if not (isinstance(pid, int) and not isinstance(pid, bool) and 0 < pid < 2**31):
        pid = None
    pid_start = record.get("pid_start")
    if not isinstance(pid_start, str):
        pid_start = None
    return {**record, "pid": pid, "pid_start": pid_start}


def latest_state(hub: Path, runs_rel: str, bead: str) -> dict[str, Any] | None:
    """The state record of the highest attempt, or None when there is none.

    ``pid`` and ``pid_start`` are normalized (SPEC §8.3) before return, so
    every caller can pass them straight to ``is_pid_alive`` without
    re-validating them.
    """
    numbers = existing_attempts(runs_dir(hub, runs_rel, bead))
    if not numbers:
        return None
    return _normalize_liveness_fields(read_state(attempt_dir(hub, runs_rel, bead, numbers[-1])))


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


_PS_LOCALE_ENV = {"LC_ALL": "C", "LANG": "C", "LC_TIME": "C", "TZ": "UTC0"}


def read_pid_start(pid: int) -> str | None:
    """Stripped ``ps -o lstart= -p <pid>`` text, or null on failure, a 5 s
    timeout, or empty output (SPEC §7.1 step 7). Always run in a fixed C
    locale and UTC time zone, at both the launch write and every later
    compare, so the text never depends on the caller's locale or time zone.
    A small wrapper so tests can monkeypatch the ``ps`` call instead of the
    real process table.
    """
    env = {**os.environ, **_PS_LOCALE_ENV}
    try:
        proc = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    return text or None


def _leader_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def is_pid_alive(pid: int | None, pid_start: str | None = None) -> bool:
    """Attempt liveness (SPEC §8.2, §9.2): the shared rule every caller uses.

    True when the leader pid exists and its current ``ps -o lstart=`` text
    equals ``pid_start``; or, when the leader no longer exists, or
    ``pid_start`` is null, when the process group is alive
    (``os.killpg(pid, 0)`` succeeds or raises ``PermissionError``). A leader
    pid whose start time differs from ``pid_start`` is a reused pid: never
    live, and helios never signals it. A null ``pid_start`` falls back to
    the process-group test alone. When the leader exists but the read comes
    back null anyway (``ps`` is missing, times out, or the leader exits
    between the two checks so ``ps`` finds nothing), fall back to the
    process-group test too, instead of reading the attempt as dead.
    """
    if pid is None:
        return False
    if pid_start is not None and _leader_exists(pid):
        current = read_pid_start(pid)
        if current is not None:
            return current == pid_start
    try:
        os.killpg(pid, 0)
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
