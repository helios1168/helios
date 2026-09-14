"""``helios resume`` (SPEC §9.2): continue a session with delivered messages or text.

Delivery and acks read and write through ``helios.messages`` without editing
it; the turn itself runs SPEC §7.1 steps 5-11 through ``helios.run.run_turn``.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

from helios import attempt as attempt_mod
from helios import beads as beads_mod
from helios import config as config_mod
from helios import run as run_mod
from helios import worktree as worktree_mod

_MSG_NAME_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.json$")

DEFAULT_TEXT = "Continue."


class ResumeRefusal(ValueError):
    """A SPEC §9.2 refusal; the command layer prints it and exits 2."""


def _latest_finalized(
    hub: Path, runs_rel: str, bead_id: str
) -> tuple[Path, dict[str, object]]:
    """The bead's highest attempt, refusing per SPEC §9.2 unless it can be resumed."""
    numbers = attempt_mod.existing_attempts(hub / runs_rel / bead_id)
    if not numbers:
        raise ResumeRefusal(f"no attempts for {bead_id}")
    n = numbers[-1]
    attempt_dir = attempt_mod.attempt_dir(hub, runs_rel, bead_id, n)
    state = attempt_mod.read_state(attempt_dir)
    if attempt_mod.is_pid_alive(state.get("pid")):
        raise ResumeRefusal(f"attempt {state.get('attempt_id')} is still running")
    if state.get("state") != "finalized":
        raise ResumeRefusal(f"attempt {state.get('attempt_id')} is not finalized")
    if not isinstance(state.get("session_id"), str):
        raise ResumeRefusal(f"no session recorded for {state.get('attempt_id')}")
    return attempt_dir, state


def _harness_of(attempt_dir: Path) -> str:
    try:
        value = json.loads((attempt_dir / "input.json").read_text()).get("harness")
    except (OSError, json.JSONDecodeError, ValueError):
        value = None
    if not isinstance(value, str) or not value:
        raise ResumeRefusal(f"no harness recorded for {attempt_dir.name}")
    return value


def _inbox_ids(inbox: Path) -> list[str]:
    """Message ids matching the SPEC §9.4 name shape, sorted as strings, listed once."""
    if not inbox.is_dir():
        return []
    return sorted(p.name[: -len(".json")] for p in inbox.iterdir() if _MSG_NAME_RE.fullmatch(p.name))


def _load_message(path: Path) -> dict[str, object] | None:
    """Parse one SPEC §9.4 message; ``None`` when it is not a valid one."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not all(isinstance(data.get(key), str) for key in ("id", "kind", "text")):
        return None
    return data


def _write_ack(runs: Path, bead_id: str, msg_id: str) -> None:
    directory = runs / bead_id / "acks"
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix="ack.", suffix=".tmp")
    try:
        os.close(fd)
        os.replace(tmp, directory / msg_id)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_ACK_STATUSES = {"completed", "missing_output", "invalid_output"}


def resume(
    bead_id: str,
    text: str | None,
    *,
    hub: Path,
    beads: beads_mod.BeadsLike,
    config: config_mod.Config,
) -> int:
    """``helios resume <bead> ["<text>"]`` (SPEC §9.2). Raises ``ResumeRefusal`` for exit 2."""
    runs_rel = config.project.runs
    runs = hub / runs_rel
    try:
        bead = beads.show(bead_id)
    except Exception as exc:
        raise ResumeRefusal(str(exc) or type(exc).__name__) from exc
    if bead.status == "closed":
        raise ResumeRefusal(f"bead {bead_id} is closed")
    attempt_dir, state = _latest_finalized(hub, runs_rel, bead_id)
    harness_name = _harness_of(attempt_dir)
    session_id = str(state["session_id"])
    resumed_from = str(state.get("attempt_id") or attempt_dir.name)

    lock = run_mod._BeadLock(hub, runs_rel, bead_id)
    if not lock.acquire(create=True):
        raise ResumeRefusal(f"bead lock held for {bead_id}")
    try:
        info = worktree_mod.prepare(
            hub=hub,
            bead=bead_id,
            worktrees=config.project.worktrees,
            link_into_worktrees=config.project.link_into_worktrees,
            again=False,
        )
        codes: list[int] = []
        delivered = 0
        for msg_id in _inbox_ids(runs / bead_id / "inbox"):
            if (runs / bead_id / "acks" / msg_id).exists():
                continue
            message = _load_message(runs / bead_id / "inbox" / f"{msg_id}.json")
            if message is None:
                print(f"helios: invalid message {msg_id}, skipping", file=sys.stderr)
                continue
            delivered += 1
            turn_text = f"[helios-msg {msg_id}] {message['text']}"
            code, execution_status = run_mod.run_turn(
                bead_id,
                hub=hub,
                beads=beads,
                config=config,
                bead=bead,
                harness_name=harness_name,
                worktree_path=info.path,
                resume_session=session_id,
                text=turn_text,
                msg_id=msg_id,
                resumed_from=resumed_from,
            )
            codes.append(code)
            if execution_status in _ACK_STATUSES:
                _write_ack(runs, bead_id, msg_id)
            else:
                return code
        if text is not None or delivered == 0:
            code, _status = run_mod.run_turn(
                bead_id,
                hub=hub,
                beads=beads,
                config=config,
                bead=bead,
                harness_name=harness_name,
                worktree_path=info.path,
                resume_session=session_id,
                text=text if text is not None else DEFAULT_TEXT,
                msg_id=None,
                resumed_from=resumed_from,
            )
            codes.append(code)
        return max(codes, default=0)
    finally:
        lock.release()
