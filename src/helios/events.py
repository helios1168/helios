"""Event stream (SPEC §9.3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from helios.attempt import utc_now

TYPES = frozenset(
    {
        "launched",
        "completed",
        "needs_input",
        "needs_review",
        "failed",
        "finalized",
        "steer",
        "answer",
        "idle",
        "error",
    }
)

SOURCES = frozenset({"helios", "opencode-plugin", "orchestrator"})

EVENT_PATH = Path(".helios/events.jsonl")
MAX_BYTES = 4096


def append(
    hub: Path,
    *,
    source: str,
    type: str,
    bead: str,
    attempt: str | None = None,
    session: str | None = None,
    detail: str = "",
) -> dict[str, Any]:
    """Append one JSON object per line with a single ``write`` under 4 KB."""
    if type not in TYPES:
        raise ValueError(f"unknown event type {type!r}")
    if source not in SOURCES:
        raise ValueError(f"unknown event source {source!r}")
    event: dict[str, Any] = {
        "ts": utc_now(),
        "source": source,
        "type": type,
        "bead": bead,
        "attempt": attempt,
        "session": session,
        "detail": detail,
    }
    line = json.dumps(event, sort_keys=True)
    while len(line.encode("utf-8")) >= MAX_BYTES and event["detail"]:
        event["detail"] = event["detail"][: len(event["detail"]) // 2]
        line = json.dumps(event, sort_keys=True)
    line += "\n"
    path = hub / EVENT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(line)
    return event
