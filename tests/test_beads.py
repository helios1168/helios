"""Beads wrapper and write-back tests (SPEC §7.1 step 1, §7.5)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from helios.beads import (
    Bead,
    Beads,
    Comment,
    FakeBeads,
    apply_writeback,
    comment_has,
    decode_metadata,
    plan_writeback,
)

HEL_A6Z = "hel-a6z"


def test_metadata_json_strings_are_decoded() -> None:
    raw = {"files": '["src/a.py"]', "attempt": "3", "author": "codex", "unit": "null"}
    assert decode_metadata(raw) == {
        "files": ["src/a.py"],
        "attempt": 3,
        "author": "codex",
        "unit": None,
    }


def test_bead_from_show_reads_metadata_and_labels() -> None:
    payload = {
        "id": HEL_A6Z,
        "title": "t",
        "description": "d",
        "status": "in_progress",
        "labels": ["kind:impl"],
        "metadata": {
            "kind": "impl",
            "files": '["src/helios/config.py"]',
            "test": "uv run pytest -q",
            "docs": ["docs/SPEC.md#5."],
            "memories": "[]",
            "author": "opencode",
        },
    }
    bead = Bead.from_show(payload)
    assert bead.files == ["src/helios/config.py"]
    assert bead.test == "uv run pytest -q"
    assert bead.docs == ["docs/SPEC.md#5."]
    assert bead.author == "opencode"


def test_replay_skips_existing_kind_and_marker() -> None:
    fake = FakeBeads([Bead(id="b1")])
    plan = plan_writeback(
        attempt_id="b1#1",
        bead_kind="impl",
        harness="codex",
        session_id="s1",
        worktree="/wt",
        attempt=1,
        execution_status="completed",
        verdict=None,
        output_commit=None,
        report_status="done",
        report_summary="did it",
        learned=["a", "b"],
        missing_context=[],
        followups=[],
        question=None,
        checks_passed=True,
    )
    assert apply_writeback(fake, "b1", plan) == 3
    assert fake.closed["b1"] == "did it"
    assert fake.beads["b1"].metadata["session"] == "codex:s1"
    # Replay is idempotent: no new comments, metadata write repeats harmlessly.
    assert apply_writeback(fake, "b1", plan) == 0
    assert len(fake.comments("b1")) == 3


def test_non_closing_plan_records_run_state() -> None:
    fake = FakeBeads([Bead(id="b2")])
    plan = plan_writeback(
        attempt_id="b2#1",
        bead_kind="verify-code",
        harness="claude",
        session_id=None,
        worktree="/wt",
        attempt=1,
        execution_status="completed",
        verdict="inconclusive",
        output_commit=None,
        report_status="done",
        report_summary="not yet",
        learned=[],
        missing_context=["x"],
        followups=[],
        question="which?",
        checks_passed=True,
    )
    assert not plan.close
    assert plan.run_state == "waiting"
    assert any(t.startswith("question: [b2#1]") for t in plan.comments)
    apply_writeback(fake, "b2", plan)
    assert "b2" not in fake.closed
    assert fake.states["b2"]["run"] == "waiting"


def test_comment_has_matches_kind_and_marker() -> None:
    comments = [Comment(id="c", issue_id="b", author="a", text="learned: [b#1#2] x")]
    assert comment_has(comments, "learned", "[b#1#2]")
    assert not comment_has(comments, "learned", "[b#1#3]")
    assert not comment_has(comments, "followup", "[b#1#2]")


BD = shutil.which("bd")


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_show_round_trip(tmp_path: Path) -> None:
    subprocess.run(["bd", "init"], cwd=tmp_path, check=True, capture_output=True)
    created = subprocess.run(
        ["bd", "create", "--title", "probe", "--type", "task", "--json"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    bead_id = json.loads(created.stdout)["id"] if created.stdout.strip().startswith("{") else created.stdout.strip().split()[-1]
    beads = Beads(tmp_path)
    bead = beads.show(bead_id)
    assert bead.id == bead_id
    assert bead.title == "probe"
