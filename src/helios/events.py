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
    """Append one JSON object per line with a single ``write`` (SPEC §9.3).

    The line, newline included, is at most 4096 bytes: ``detail`` is
    shortened first, and the call raises when the line is still too long.
    """
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
    line = _encode(event)
    while len(line) > MAX_BYTES and event["detail"]:
        event["detail"] = event["detail"][: len(event["detail"]) // 2]
        line = _encode(event)
    if len(line) > MAX_BYTES:
        raise ValueError(f"event line for bead {bead!r} exceeds {MAX_BYTES} bytes")
    path = hub / EVENT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(line.decode("utf-8"))
    return event


def _encode(event: dict[str, Any]) -> bytes:
    return (json.dumps(event, sort_keys=True) + "\n").encode("utf-8")
