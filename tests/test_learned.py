from __future__ import annotations

import pytest

from helios.beads import Bead, Comment, FakeBeads
from helios.learned import list_lines, mark


def test_learned_multiline_dedupes_and_sorts() -> None:
    beads = FakeBeads([Bead("b", kind="impl", labels=["kind:impl", "unit:u"])])
    beads._comments["b"] = [
        Comment("1", "b", "a", "learned: [b#2#1] first\nsecond"),
        Comment("2", "b", "a", "learned: [b#2#1] duplicate"),
        Comment("3", "b", "a", "missing_context: [b#1#1] context"),
    ]
    lines = list_lines(beads)
    assert [(x.attempt, x.kind, x.text) for x in lines] == [(1, "missing_context", "context"), (2, "learned", "first\nsecond")]


def test_mark_replays_and_adds_curated_label() -> None:
    beads = FakeBeads([Bead("b", kind="impl", labels=["kind:impl", "unit:u"])])
    beads.add_comment("b", "learned: [b#1#1] remember this")
    assert mark(beads, "learned:b#1#1", "memory") == 0
    assert mark(beads, "learned:b#1#1", "memory") == 0
    assert "curated" in beads.show("b").labels
    assert len(beads.comments("b")) == 2


def test_unknown_mark_is_value_error() -> None:
    with pytest.raises(ValueError):
        mark(FakeBeads(), "learned:nope#1#1", "drop")
