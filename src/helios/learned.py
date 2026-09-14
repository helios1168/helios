"""Learned queue parsing and curation (SPEC section 14)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from helios.beads import Bead, BeadsLike
from helios.stages import STAGES

LINE_RE = re.compile(r"^(learned|missing_context): \[(.+)#(\d+)#(\d+)\] (.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class LearnedLine:
    unit: str
    bead: str
    attempt: int
    kind: str
    k: int
    text: str

    def as_json(self) -> dict[str, Any]:
        return {"unit": self.unit, "bead": self.bead, "attempt": self.attempt, "kind": self.kind, "k": self.k, "text": self.text}


def _lines(beads: Any, unit: str | None = None) -> list[tuple[Bead, LearnedLine]]:
    found: dict[tuple[str, str, int, int], tuple[Bead, LearnedLine]] = {}
    for bead in beads.list():
        if bead.kind not in STAGES or f"kind:{bead.kind}" not in bead.labels:
            continue
        if unit is not None and f"unit:{unit}" not in bead.labels:
            continue
        bead_unit = next((label[5:] for label in bead.labels if label.startswith("unit:")), "-")
        curated = {c.text for c in beads.comments(bead.id) if c.text.startswith("curated: ")}
        for comment in beads.comments(bead.id):
            match = LINE_RE.fullmatch(comment.text)
            if not match:
                continue
            kind, attempt_id, attempt, k, text = match.groups()
            marker = f"{kind}:{attempt_id}#{attempt}#{k}"
            if any(mark.startswith(f"curated: [{marker}]") for mark in curated):
                continue
            item = LearnedLine(bead_unit, bead.id, int(attempt), kind, int(k), text)
            found.setdefault((bead.id, kind, int(attempt), int(k)), (bead, item))
    return sorted(found.values(), key=lambda pair: (pair[1].unit, pair[1].bead, pair[1].attempt, pair[1].kind, pair[1].k))


def list_lines(beads: Any, unit: str | None = None) -> list[LearnedLine]:
    """Return the curated-filtered learned queue."""
    return [item for _bead, item in _lines(beads, unit)]


def mark(beads: Any, marker: str, decision: str) -> int:
    """Curate one queue entry, replaying an existing mark as a no-op."""
    if decision not in {"memory", "template", "drop"}:
        raise ValueError(f"unknown decision {decision}")
    match = re.fullmatch(r"(learned|missing_context):(.+#\d+)#(\d+)", marker)
    if not match:
        raise ValueError(f"unknown marker {marker}")
    kind, attempt_id, k = match.groups()
    target = f"curated: [{kind}:{attempt_id}#{k}] -> {decision}"
    for bead in beads.list():
        if any(c.text.startswith(f"curated: [{kind}:{attempt_id}#{k}]") for c in beads.comments(bead.id)):
            return 0
    for bead, item in _lines(beads):
        if item.kind == kind and item.bead == attempt_id.rsplit("#", 1)[0] and item.attempt == int(attempt_id.rsplit("#", 1)[1]) and item.k == int(k):
            comments = beads.comments(bead.id)
            if any(c.text.startswith(f"curated: [{kind}:{attempt_id}#{k}]") for c in comments):
                return 0
            beads.add_comment(bead.id, target)
            remaining = _lines(beads, next((x.unit for x in [item] if x.unit != "-"), None))
            if not any(pair[0].id == bead.id for pair in remaining):
                beads.add_label(bead.id, "curated")
            return 0
    raise ValueError(f"unknown marker {marker}")
