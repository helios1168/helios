from __future__ import annotations

import json
import shutil
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from helios.beads import Bead, Comment, FakeBeads
from helios.learned import list_lines, mark


def queue_beads() -> FakeBeads:
    return FakeBeads([
        Bead("b", kind="impl", labels=["kind:impl", "unit:u"]),
        Bead("ignored", kind="impl", labels=["unit:u"]),
        Bead("closed", kind="impl", status="closed", labels=["kind:impl"]),
    ])


def test_learned_dotall_dedupes_by_kind_marker_and_curated_kind() -> None:
    beads = queue_beads()
    beads._comments["b"] = [
        Comment("1", "b", "a", "learned: [b#10#2] ten\nlines"),
        Comment("2", "b", "a", "learned: [b#2#1] two"),
        Comment("3", "b", "a", "learned: [b#2#1] duplicate"),
        Comment("4", "b", "a", "missing_context: [b#2#1] keep despite learned mark"),
        Comment("5", "b", "a", "curated: [learned:b#10#2] -> memory"),
    ]
    lines = list_lines(beads)
    assert [(line.attempt, line.kind, line.k, line.text) for line in lines] == [
        (2, "learned", 1, "two"),
        (2, "missing_context", 1, "keep despite learned mark"),
    ]


def test_learned_unit_filter_and_status_and_unlabelled_filter() -> None:
    beads = queue_beads()
    beads.add_comment("b", "learned: [b#1#1] u")
    beads.add_comment("closed", "learned: [closed#1#1] closed")
    assert [line.bead for line in list_lines(beads, "u")] == ["b"]
    assert [line.bead for line in list_lines(beads)] == ["b", "closed"]


def test_learned_mark_comment_replay_unknown_and_curated_label() -> None:
    beads = FakeBeads([Bead("b", kind="impl", labels=["kind:impl", "unit:u"])])
    beads.add_comment("b", "learned: [b#1#1] one")
    beads.add_comment("b", "missing_context: [b#1#1] two")
    assert mark(beads, "learned:b#1#1", "memory") == 0
    assert beads.comments("b")[-1].text == "curated: [learned:b#1#1] -> memory"
    assert "curated" not in beads.show("b").labels
    assert mark(beads, "learned:b#1#1", "memory") == 0
    assert len(beads.comments("b")) == 3
    assert mark(beads, "missing_context:b#1#1", "drop") == 0
    assert beads.comments("b")[-1].text == "curated: [missing_context:b#1#1] -> drop"
    assert "curated" in beads.show("b").labels
    with pytest.raises(ValueError):
        mark(beads, "learned:nope#1#1", "drop")


def test_learned_command_json_and_text_grouping(monkeypatch, capsys) -> None:
    from helios.commands import learned as command

    fake = FakeBeads([Bead("b", kind="impl", labels=["kind:impl", "unit:u"])])
    fake.add_comment("b", "learned: [b#2#1] text")
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)
    monkeypatch.setattr(command, "load", lambda _path: Namespace(hub=Path(".")))
    assert command.run(Namespace(unit=None, json=True, mark=None, decision=None)) == 0
    assert json.loads(capsys.readouterr().out) == [{"unit": "u", "bead": "b", "attempt": 2, "kind": "learned", "k": 1, "text": "text"}]
    assert command.run(Namespace(unit=None, json=False, mark=None, decision=None)) == 0
    assert capsys.readouterr().out == "unit: u\n  bead: b\n    attempt: 2\n      learned#1: text\n"


def test_learned_command_mark_flags_exit_two_and_unknown_marker_prefixed(monkeypatch, capsys) -> None:
    from helios.commands import learned as command

    fake = FakeBeads()
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)
    monkeypatch.setattr(command, "load", lambda _path: Namespace(hub=Path(".")))
    for args in (
        Namespace(unit="u", json=False, mark="learned:b#1#1", decision="drop"),
        Namespace(unit=None, json=True, mark="learned:b#1#1", decision="drop"),
    ):
        assert command.run(args) == 2
        assert capsys.readouterr().err.startswith("helios: ")
    assert command.run(Namespace(unit=None, json=False, mark="learned:b#1#1", decision="drop")) == 2
    assert capsys.readouterr().err.startswith("helios: ")


BD = shutil.which("bd")


def init_bd(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["bd", "init", "--non-interactive", "--prefix", "t", "--skip-agents", "--quiet"], cwd=path, check=True, capture_output=True)


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_learned_comments_mark_and_label(tmp_path: Path) -> None:
    init_bd(tmp_path)
    created = subprocess.run(["bd", "create", "--title", "learn", "--labels", "kind:impl,unit:u", "--silent"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()
    beads = __import__("helios.beads", fromlist=["Beads"]).Beads(tmp_path)
    beads.add_comment(created, f"learned: [{created}#1#1] text")
    assert [line.text for line in list_lines(beads)] == ["text"]
    assert mark(beads, f"learned:{created}#1#1", "memory") == 0
    assert "curated" in beads.show(created).labels
