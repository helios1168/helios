"""Tests for helios.events.append (SPEC 9.3), including the torn-tail fix (hel-n0f).

A crash can leave the last line of the events file without a trailing newline.
The next append must not glue its line onto that partial one.
"""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest

from helios import events


def _read_lines(hub: Path) -> list[bytes]:
    raw = (hub / ".helios" / "events.jsonl").read_bytes()
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return lines


def test_append_after_torn_tail_keeps_partial_line_and_parses_new_event(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / ".helios" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    partial = b'{"ts": "2026-01-01T00:00:00Z", "source": "helios", "type": "launched", "bead": "b1"'
    events_path.write_bytes(partial)

    events.append(tmp_path, source="helios", type="completed", bead="b1", attempt="b1#1")

    lines = _read_lines(tmp_path)
    assert len(lines) == 2
    assert lines[0] == partial
    with pytest.raises(json.JSONDecodeError):
        json.loads(lines[0])
    last = json.loads(lines[1])
    assert last["type"] == "completed" and last["bead"] == "b1"


def test_append_no_extra_blank_line_for_empty_or_newline_terminated_file(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / ".helios" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_bytes(b"")

    events.append(tmp_path, source="helios", type="launched", bead="b1")
    lines = _read_lines(tmp_path)
    assert len(lines) == 1
    assert lines[0] != b""

    # The file now ends with a newline; the next append must add no blank line.
    events.append(tmp_path, source="helios", type="completed", bead="b1")
    lines = _read_lines(tmp_path)
    assert len(lines) == 2
    assert all(line != b"" for line in lines)


def _append_many(hub: Path, worker: int, count: int) -> None:
    for i in range(count):
        events.append(
            hub,
            source="helios",
            type="launched",
            bead="b1",
            attempt=f"w{worker}#{i}",
        )


def test_concurrent_appends_after_torn_tail_never_interleave(tmp_path: Path) -> None:
    events_path = tmp_path / ".helios" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    partial = b'{"ts": "2026-01-01T00:00:00Z", "source": "helios", "type": "launched", "bead": "torn"'
    events_path.write_bytes(partial)

    workers, per_worker = 8, 200
    ctx = multiprocessing.get_context("fork")
    procs = [
        ctx.Process(target=_append_many, args=(tmp_path, w, per_worker))
        for w in range(workers)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
        assert p.exitcode == 0

    lines = _read_lines(tmp_path)
    assert len(lines) == 1 + workers * per_worker

    parsed = 0
    unparsed = 0
    seen: set[tuple[int, int]] = set()
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            unparsed += 1
            assert line == partial
            continue
        parsed += 1
        w_str, i_str = obj["attempt"].split("#")
        seen.add((int(w_str[1:]), int(i_str)))

    assert unparsed == 1
    assert parsed == workers * per_worker
    assert seen == {(w, i) for w in range(workers) for i in range(per_worker)}


def test_size_limit_unchanged_and_separator_not_counted(tmp_path: Path) -> None:
    events_path = tmp_path / ".helios" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_bytes(b"torn-no-newline")

    events.append(tmp_path, source="helios", type="launched", bead="b1", detail="x" * 4090)
    raw = (tmp_path / ".helios" / "events.jsonl").read_bytes()
    file_lines = raw.split(b"\n")
    assert file_lines[-1] == b""
    appended_line = file_lines[-2] + b"\n"
    assert len(appended_line) <= 4096

    with pytest.raises(ValueError):
        events.append(tmp_path, source="helios", type="launched", bead="b" * 5000)
