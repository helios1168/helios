"""Orchestrator messages (SPEC §9.4)."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from helios import attempt, beads, events


def _utc_seconds() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_message(runs: Path, bead: str, text: str, kind: str = "steer") -> str:
    """Atomically write one inbox message and return its id (SPEC §9.4)."""
    created = _utc_seconds()
    stamp = created.replace("-", "").replace(":", "")
    msg_id = f"{stamp}-{secrets.token_hex(4)}"
    directory = runs / bead / "inbox"
    directory.mkdir(parents=True, exist_ok=True)
    record = {"id": msg_id, "kind": kind, "text": text, "created": created,
              "from": "orchestrator", "to": bead}
    fd, temporary = tempfile.mkstemp(dir=directory, prefix="message.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(record, handle, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, directory / f"{msg_id}.json")
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return msg_id


def add_comment(bead_store: beads.BeadsLike, bead: str, kind: str, msg_id: str, text: str) -> None:
    """Add the replay-safe message comment (SPEC §9.4 and §7.5)."""
    comment = f"{kind}: [{msg_id}] {text}"
    if not beads.comment_has(bead_store.comments(bead), kind, f"[{msg_id}]"):
        bead_store.add_comment(bead, comment)


def record_event(hub: Path, bead: str, attempt_id: str, kind: str, msg_id: str) -> None:
    events.append(hub, source="orchestrator", type=kind, bead=bead,
                  attempt=attempt_id, detail=msg_id)


def say(
    hub: Path,
    runs_rel: str,
    bead: str,
    text: str,
    *,
    kind: str = "steer",
    bead_store: beads.BeadsLike | None = None,
    pid_alive: Callable[[int | None], bool] | None = None,
) -> tuple[str, bool]:
    """Write, comment, and event a message without delivering it (SPEC §9.4)."""
    numbers = attempt.existing_attempts(hub / runs_rel / bead)
    if not numbers:
        raise ValueError(f"no runs directory for {bead}")
    attempt_dir = hub / runs_rel / bead / f"attempt-{numbers[-1]}"
    state = _read_state(attempt_dir)
    msg_id = write_message(hub / runs_rel, bead, text, kind)
    store = bead_store or beads.Beads(hub)
    attempt_id = str(state.get("attempt_id") or f"{bead}#?")
    add_comment(store, bead, kind, msg_id, text)
    record_event(hub, bead, attempt_id, kind, msg_id)
    alive = (pid_alive or _pid_alive)(state.get("pid"))
    return msg_id, state.get("state") == "launched" and alive


def _read_state(directory: Path) -> dict[str, Any]:
    try:
        value = json.loads((directory / "state.json").read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
