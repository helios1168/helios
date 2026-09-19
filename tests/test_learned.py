from __future__ import annotations

import json
import shutil
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from helios import stageset
from helios.beads import Bead, BeadNotFound, Comment, FakeBeads
from helios.learned import LINE_RE, list_lines, mark

#: Every test here exercises research stage behavior, so every direct call passes this
#: explicitly now that list_lines/mark take no default.
RESEARCH = stageset.research()


def queue_beads() -> FakeBeads:
    return FakeBeads([
        Bead("b", kind="impl", labels=["kind:impl", "unit:u"]),
        Bead("ignored", kind="impl", labels=["unit:u"]),
        Bead("closed", kind="impl", status="closed", labels=["kind:impl"]),
    ])


def test_learned_dotall_dedupes_by_kind_marker_and_curated_kind() -> None:
    """The multi-line greedy case: DOTALL lets '.' cross the newline inside a text tail."""
    beads = queue_beads()
    beads._comments["b"] = [
        Comment("1", "b", "a", "learned: [b#2#1] two\nlines"),
        Comment("2", "b", "a", "learned: [b#2#1] duplicate"),
        Comment("3", "b", "a", "missing_context: [b#2#1] keep despite learned mark"),
        Comment("4", "b", "a", "curated: [learned:b#2#1] -> memory"),
    ]
    lines = list_lines(beads, stages=RESEARCH)
    assert [(line.attempt, line.kind, line.k, line.text) for line in lines] == [
        (2, "missing_context", 1, "keep despite learned mark"),
    ]


def test_learned_unit_filter_and_status_and_unlabelled_filter() -> None:
    beads = queue_beads()
    beads.add_comment("b", "learned: [b#1#1] u")
    beads.add_comment("closed", "learned: [closed#1#1] closed")
    beads.add_comment("ignored", "learned: [ignored#1#1] no kind label")
    assert [line.bead for line in list_lines(beads, "u", stages=RESEARCH)] == ["b"]
    # "closed" carries no unit label, so its unit sorts as "-", ahead of "u".
    assert [(line.unit, line.bead) for line in list_lines(beads, stages=RESEARCH)] == [("-", "closed"), ("u", "b")]


def test_learned_sort_order_numeric_attempt_before_ten() -> None:
    beads = FakeBeads([Bead("b", kind="impl", labels=["kind:impl", "unit:u"])])
    beads.add_comment("b", "learned: [b#10#1] ten")
    beads.add_comment("b", "learned: [b#2#1] two")
    assert [line.attempt for line in list_lines(beads, stages=RESEARCH)] == [2, 10]


def test_learned_bead_field_is_marker_bead_not_carrier() -> None:
    """Decided: the listed bead is the bead named in the marker, not the carrier."""
    beads = FakeBeads([Bead("carrier", kind="impl", labels=["kind:impl", "unit:u"])])
    beads.add_comment("carrier", "learned: [other#1#1] x")
    lines = list_lines(beads, stages=RESEARCH)
    assert [line.bead for line in lines] == ["other"]


def test_learned_leading_zero_markers_are_distinct() -> None:
    """Decided: [b#01#1] and [b#1#1] are distinct markers, not deduped against each other."""
    beads = FakeBeads([Bead("b", kind="impl", labels=["kind:impl", "unit:u"])])
    beads.add_comment("b", "learned: [b#01#1] zero")
    beads.add_comment("b", "learned: [b#1#1] plain")
    lines = list_lines(beads, stages=RESEARCH)
    assert len(lines) == 2
    assert [line.text for line in lines] == ["zero", "plain"]  # marker text tiebreak: "01" < "1"


def test_learned_curated_comment_on_marker_target_without_kind_label() -> None:
    """Decided: the curated set also includes comments of every bead a queue marker
    names, even without a helios kind label of its own."""
    beads = FakeBeads([
        Bead("carrier", kind="impl", labels=["kind:impl", "unit:u"]),
        Bead("other", labels=[]),
    ])
    beads.add_comment("carrier", "learned: [other#1#1] x")
    beads.add_comment("other", "curated: [learned:other#1#1] -> drop")
    assert list_lines(beads, stages=RESEARCH) == []


class BeadNotFoundForOneBead(FakeBeads):
    def __init__(self, beads: list[Bead], missing: str) -> None:
        super().__init__(beads)
        self.missing = missing

    def comments(self, bead_id: str) -> list[Comment]:
        if bead_id == self.missing:
            raise BeadNotFound(bead_id)
        return super().comments(bead_id)


def test_learned_marker_target_bead_not_found_is_skipped_not_raised() -> None:
    """Decided (round 3, item 4): a queue marker naming a bead that no longer exists
    must not crash the listing; that one bead's (unreachable) curated comments are
    simply skipped."""
    beads = BeadNotFoundForOneBead([Bead("carrier", kind="impl", labels=["kind:impl", "unit:u"])], missing="gone")
    beads.add_comment("carrier", "learned: [gone#1#1] x")
    lines = list_lines(beads, stages=RESEARCH)
    assert [line.bead for line in lines] == ["gone"]


class RuntimeErrorForOneBead(FakeBeads):
    def __init__(self, beads: list[Bead], missing: str) -> None:
        super().__init__(beads)
        self.missing = missing

    def comments(self, bead_id: str) -> list[Comment]:
        if bead_id == self.missing:
            raise RuntimeError("bd comments gone failed: no such issue")
        return super().comments(bead_id)


def test_learned_marker_target_bead_runtime_error_is_skipped_not_raised() -> None:
    beads = RuntimeErrorForOneBead([Bead("carrier", kind="impl", labels=["kind:impl", "unit:u"])], missing="gone")
    beads.add_comment("carrier", "learned: [gone#1#1] x")
    lines = list_lines(beads, stages=RESEARCH)
    assert [line.bead for line in lines] == ["gone"]


def test_learned_command_marker_target_not_found_returns_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from helios.commands import learned as command

    fake = BeadNotFoundForOneBead([Bead("carrier", kind="impl", labels=["kind:impl", "unit:u"])], missing="gone")
    fake.add_comment("carrier", "learned: [gone#1#1] x")
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)
    monkeypatch.setattr(command, "load", lambda _path: Namespace(hub=Path("."), stages=stageset.research()))
    assert command.run(Namespace(unit=None, json=True, mark=None, decision=None)) == 0
    assert json.loads(capsys.readouterr().out) == [
        {"unit": "u", "bead": "gone", "attempt": 1, "kind": "learned", "k": 1, "text": "x"}
    ]


def test_learned_curated_comment_on_another_bead_still_counts() -> None:
    """Decided: a curated: comment for a marker counts on any bead."""
    beads = FakeBeads([
        Bead("b", kind="impl", labels=["kind:impl", "unit:u"]),
        Bead("c", kind="impl", labels=["kind:impl", "unit:u"]),
    ])
    beads.add_comment("b", "learned: [b#1#1] x")
    beads.add_comment("c", "curated: [learned:b#1#1] -> drop")
    assert list_lines(beads, stages=RESEARCH) == []


def test_learned_regex_ascii_digits_only_ten_digit_attempt_no_match() -> None:
    """Decided: attempt/k are ASCII digits, at most 9 of them; longer runs do not match."""
    big = "1" * 10
    beads = FakeBeads([Bead("b", kind="impl", labels=["kind:impl", "unit:u"])])
    beads.add_comment("b", f"learned: [b#{big}#1] x")
    assert list_lines(beads, stages=RESEARCH) == []
    assert LINE_RE.fullmatch(f"learned: [b#{big}#1] x") is None


def test_learned_regex_unicode_digits_no_match() -> None:
    beads = FakeBeads([Bead("b", kind="impl", labels=["kind:impl", "unit:u"])])
    beads.add_comment("b", "learned: [b#１#1] x")  # fullwidth "1", not ASCII
    assert list_lines(beads, stages=RESEARCH) == []


def test_learned_mark_comment_text_replay_unknown_and_curated_label() -> None:
    beads = FakeBeads([Bead("b", kind="impl", labels=["kind:impl", "unit:u"])])
    beads.add_comment("b", "learned: [b#1#1] one")
    beads.add_comment("b", "missing_context: [b#1#1] two")
    assert mark(beads, "learned:b#1#1", "memory", stages=RESEARCH) == 0
    assert beads.comments("b")[-1].text == "curated: [learned:b#1#1] -> memory"
    assert "curated" not in beads.show("b").labels
    assert mark(beads, "learned:b#1#1", "memory", stages=RESEARCH) == 0
    assert len(beads.comments("b")) == 3
    assert mark(beads, "missing_context:b#1#1", "drop", stages=RESEARCH) == 0
    assert beads.comments("b")[-1].text == "curated: [missing_context:b#1#1] -> drop"
    assert "curated" in beads.show("b").labels
    with pytest.raises(ValueError):
        mark(beads, "learned:nope#1#1", "drop", stages=RESEARCH)


def test_learned_mark_unknown_marker_text_variants_are_rejected() -> None:
    """Decided: --mark compares marker text exactly, never parsed numbers, so a
    leading-zero or Unicode-digit variant of a real marker is unknown, not curated."""
    beads = FakeBeads([Bead("t-a7v", kind="impl", labels=["kind:impl", "unit:u"])])
    beads.add_comment("t-a7v", "learned: [t-a7v#1#1] only line")
    with pytest.raises(ValueError, match="unknown marker"):
        mark(beads, "learned:t-a7v#01#1", "drop", stages=RESEARCH)
    with pytest.raises(ValueError, match="unknown marker"):
        mark(beads, "learned:t-a7v#1#01", "drop", stages=RESEARCH)
    with pytest.raises(ValueError, match="unknown marker"):
        mark(beads, "learned:t-a7v#١#1", "drop", stages=RESEARCH)  # Arabic-Indic digit one
    assert len(beads.comments("t-a7v")) == 1  # nothing written by the rejected marks


def test_learned_mark_replay_of_curated_marker_still_matches() -> None:
    """Decided: --mark validates against the full listing including curated markers,
    so a replay of an already-curated marker is still known, not unknown."""
    beads = FakeBeads([Bead("b", kind="impl", labels=["kind:impl", "unit:u"])])
    beads.add_comment("b", "learned: [b#1#1] x")
    beads.add_comment("b", "curated: [learned:b#1#1] -> drop")
    assert "curated" not in beads.show("b").labels
    assert mark(beads, "learned:b#1#1", "drop", stages=RESEARCH) == 0
    assert "curated" in beads.show("b").labels


def test_learned_mark_replay_after_crash_before_label_adds_label() -> None:
    """Decided: --mark still runs the label step when the curated comment already
    exists, so a crash between the comment and the label heals on replay."""
    beads = FakeBeads([Bead("b", kind="impl", labels=["kind:impl", "unit:u"])])
    beads.add_comment("b", "learned: [b#1#1] x")
    beads.add_comment("b", "curated: [learned:b#1#1] -> drop")  # comment landed, label did not
    assert "curated" not in beads.show("b").labels
    assert mark(beads, "learned:b#1#1", "drop", stages=RESEARCH) == 0
    assert "curated" in beads.show("b").labels
    comments_before = len(beads.comments("b"))
    assert mark(beads, "learned:b#1#1", "drop", stages=RESEARCH) == 0  # double replay changes nothing
    assert len(beads.comments("b")) == comments_before
    assert beads.show("b").labels.count("curated") == 1


def test_learned_command_json_and_text_grouping(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from helios.commands import learned as command

    fake = FakeBeads([Bead("b", kind="impl", labels=["kind:impl", "unit:u"])])
    fake.add_comment("b", "learned: [b#2#1] text")
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)
    monkeypatch.setattr(command, "load", lambda _path: Namespace(hub=Path("."), stages=stageset.research()))
    assert command.run(Namespace(unit=None, json=True, mark=None, decision=None)) == 0
    assert json.loads(capsys.readouterr().out) == [{"unit": "u", "bead": "b", "attempt": 2, "kind": "learned", "k": 1, "text": "text"}]
    assert command.run(Namespace(unit=None, json=False, mark=None, decision=None)) == 0
    assert capsys.readouterr().out == "unit: u\n  bead: b\n    attempt: 2\n      learned#1: text\n"


def test_learned_command_mark_flags_exit_two_and_unknown_marker_prefixed(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from helios.commands import learned as command

    fake = FakeBeads()
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)
    monkeypatch.setattr(command, "load", lambda _path: Namespace(hub=Path("."), stages=stageset.research()))
    for args in (
        Namespace(unit="u", json=False, mark="learned:b#1#1", decision="drop"),
        Namespace(unit=None, json=True, mark="learned:b#1#1", decision="drop"),
    ):
        assert command.run(args) == 2
        assert capsys.readouterr().err.startswith("helios: ")
    assert command.run(Namespace(unit=None, json=False, mark="learned:b#1#1", decision="drop")) == 2
    assert capsys.readouterr().err.startswith("helios: ")


class FailingList(FakeBeads):
    def list(self, *, labels: list[str] = [], status: str | None = None) -> list[Bead]:
        raise RuntimeError("bd list failed: boom")


def test_learned_command_bd_runtime_error_on_list_is_prefixed_and_exit_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Decided: a bd RuntimeError in `learned` prints `helios: <message>` and exits 1,
    never a traceback."""
    from helios.commands import learned as command

    monkeypatch.setattr(command, "Beads", lambda _hub: FailingList())
    monkeypatch.setattr(command, "load", lambda _path: Namespace(hub=Path("."), stages=stageset.research()))
    assert command.run(Namespace(unit=None, json=False, mark=None, decision=None)) == 1
    assert capsys.readouterr().err == "helios: bd list failed: boom\n"


class FailingComment(FakeBeads):
    def add_comment(self, bead_id: str, text: str) -> None:
        raise RuntimeError("bd comment failed: boom")


def test_learned_command_bd_runtime_error_on_mark_is_prefixed_and_exit_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from helios.commands import learned as command

    fake = FailingComment([Bead("b", kind="impl", labels=["kind:impl"])])
    fake._comments["b"] = [Comment("1", "b", "a", "learned: [b#1#1] x")]
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)
    monkeypatch.setattr(command, "load", lambda _path: Namespace(hub=Path("."), stages=stageset.research()))
    assert command.run(Namespace(unit=None, json=False, mark="learned:b#1#1", decision="drop")) == 1
    assert capsys.readouterr().err == "helios: bd comment failed: boom\n"


BD = shutil.which("bd")


def test_learned_mark_missing_target_bead_curates_source_and_skips_label() -> None:
    """Decided (item 1): a marker naming a bead that no longer exists writes the
    curated comment on the source bead (the one carrying the marker comment) instead,
    and skips the label step. The line then leaves the queue and a replay writes no
    second comment."""
    beads = FakeBeads([Bead("carrier", kind="impl", labels=["kind:impl", "unit:u"])])
    beads.add_comment("carrier", "learned: [gone#1#1] x")
    assert mark(beads, "learned:gone#1#1", "drop", stages=RESEARCH) == 0
    assert beads.comments("carrier")[-1].text == "curated: [learned:gone#1#1] -> drop"
    assert list_lines(beads, stages=RESEARCH) == []
    comments_before = len(beads.comments("carrier"))
    assert mark(beads, "learned:gone#1#1", "drop", stages=RESEARCH) == 0
    assert len(beads.comments("carrier")) == comments_before


def test_learned_mark_already_check_lets_runtime_error_propagate() -> None:
    """Decided (item 2): the already-curated check inside mark catches only
    BeadNotFound per bead; a bd RuntimeError for the marker's named bead propagates
    instead of being read as "not yet curated", and nothing is written."""
    beads = RuntimeErrorForOneBead(
        [
            Bead("carrier", kind="impl", labels=["kind:impl", "unit:u"]),
            Bead("target", labels=[]),
        ],
        missing="target",
    )
    beads.add_comment("carrier", "learned: [target#1#1] x")
    with pytest.raises(RuntimeError):
        mark(beads, "learned:target#1#1", "memory", stages=RESEARCH)
    assert beads.comments("carrier") == [Comment("c0", "carrier", "helios", "learned: [target#1#1] x")]
    assert beads._comments.get("target", []) == []


def init_bd(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["bd", "init", "--non-interactive", "--prefix", "t", "--skip-agents", "--quiet"], cwd=path, check=True, capture_output=True)


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_learned_comments_mark_and_label(tmp_path: Path) -> None:
    init_bd(tmp_path)
    created = subprocess.run(["bd", "create", "--title", "learn", "--labels", "kind:impl,unit:u", "--silent"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()
    beads = __import__("helios.beads", fromlist=["Beads"]).Beads(tmp_path)
    beads.add_comment(created, f"learned: [{created}#1#1] text")
    assert [line.text for line in list_lines(beads, stages=RESEARCH)] == ["text"]
    assert mark(beads, f"learned:{created}#1#1", "memory", stages=RESEARCH) == 0
    assert "curated" in beads.show(created).labels


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_learned_mark_missing_target_bead_curates_source(tmp_path: Path) -> None:
    init_bd(tmp_path)
    created = subprocess.run(["bd", "create", "--title", "learn", "--labels", "kind:impl,unit:u", "--silent"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()
    beads = __import__("helios.beads", fromlist=["Beads"]).Beads(tmp_path)
    beads.add_comment(created, "learned: [gone#1#1] text")
    assert [line.text for line in list_lines(beads, stages=RESEARCH)] == ["text"]
    assert mark(beads, "learned:gone#1#1", "drop", stages=RESEARCH) == 0
    assert beads.comments(created)[-1].text == "curated: [learned:gone#1#1] -> drop"
    assert list_lines(beads, stages=RESEARCH) == []
    comments_before = len(beads.comments(created))
    assert mark(beads, "learned:gone#1#1", "drop", stages=RESEARCH) == 0
    assert len(beads.comments(created)) == comments_before
