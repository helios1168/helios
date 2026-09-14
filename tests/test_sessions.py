"""Session and message tests (SPEC §9.2, §9.4)."""

from __future__ import annotations

import json
from pathlib import Path

from helios import attempt, messages, sessions
from helios.beads import Bead, FakeBeads


def test_message_has_spec_fields_and_replay_comment(tmp_path: Path) -> None:
    bead_dir = tmp_path / ".helios" / "runs" / "b1" / "attempt-1"
    bead_dir.mkdir(parents=True)
    attempt.write_state(bead_dir, attempt_id="b1#1", state="allocated")
    store = FakeBeads([Bead(id="b1")])
    msg_id, queued = messages.say(tmp_path, ".helios/runs", "b1", "hello", bead_store=store)
    assert not queued
    payload = json.loads((bead_dir.parent / "inbox" / f"{msg_id}.json").read_text())
    assert payload == {
        "id": msg_id,
        "kind": "steer",
        "text": "hello",
        "created": payload["created"],
        "from": "orchestrator",
        "to": "b1",
    }
    assert store.comments("b1")[0].text == f"steer: [{msg_id}] hello"


def test_rows_mark_dead_launched_attempt(tmp_path: Path) -> None:
    bead_dir = tmp_path / ".helios" / "runs" / "b1" / "attempt-1"
    bead_dir.mkdir(parents=True)
    attempt.write_state(bead_dir, attempt_id="b1#1", state="launched", pid=2)
    (bead_dir / "input.json").write_text(json.dumps({"harness": "fake", "worktree": "/wt"}))
    rows = sessions.rows(tmp_path, ".helios/runs", bead_store=FakeBeads([Bead(id="b1")]))
    assert rows[0]["state"] == "launched (dead)"
    assert rows[0]["alive"] is False
