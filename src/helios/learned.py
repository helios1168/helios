"""Learned queue parsing and curation (SPEC section 14)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from helios.beads import Bead, BeadsLike
from helios.stages import STAGES

# ASCII digits only, at most 9 of them, and the bead group cannot span "]", "#" or a newline
# (Decided, replacing the SPEC section 14 regex).
LINE_RE = re.compile(
    r"^(learned|missing_context): \[([A-Za-z0-9][A-Za-z0-9._-]*)#([0-9]{1,9})#([0-9]{1,9})\] (.*)\Z",
    re.DOTALL,
)


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


def _helios_beads(beads: Any) -> list[Bead]:
    return [bead for bead in beads.list() if bead.kind in STAGES and f"kind:{bead.kind}" in bead.labels]


def _curated_texts(beads: Any, relevant: list[Bead]) -> list[str]:
    """Every ``curated: [...]`` comment on any helios-kind bead (Decided: a curated
    comment for a marker counts on any bead, not just the one carrying the raw line)."""
    curated: list[str] = []
    for bead in relevant:
        curated.extend(c.text for c in beads.comments(bead.id) if c.text.startswith("curated: ["))
    return curated


def _lines(beads: Any, unit: str | None = None) -> list[tuple[Bead, LearnedLine]]:
    relevant = _helios_beads(beads)
    curated = _curated_texts(beads, relevant)

    found: dict[str, tuple[Bead, LearnedLine, str]] = {}
    for bead in relevant:
        if unit is not None and f"unit:{unit}" not in bead.labels:
            continue
        bead_unit = next((label[5:] for label in bead.labels if label.startswith("unit:")), "-")
        for comment in beads.comments(bead.id):
            match = LINE_RE.fullmatch(comment.text)
            if not match:
                continue
            kind, marker_bead, attempt, k, text = match.groups()
            marker = f"[{kind}:{marker_bead}#{attempt}#{k}]"
            if marker in found:
                continue  # dedupe by marker across all beads; the first in bd order wins
            if any(c.startswith(f"curated: {marker}") for c in curated):
                continue
            item = LearnedLine(bead_unit, marker_bead, int(attempt), kind, int(k), text)
            found[marker] = (bead, item, marker)
    ordered = sorted(
        found.values(),
        key=lambda triple: (triple[1].unit, triple[1].bead, triple[1].attempt, triple[1].kind, triple[1].k, triple[2]),
    )
    return [(bead, item) for bead, item, _marker in ordered]


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
    target_bead, attempt = attempt_id.rsplit("#", 1)
    prefix = f"curated: [{kind}:{attempt_id}#{k}]"

    already = any(c.startswith(prefix) for c in _curated_texts(beads, _helios_beads(beads)))
    known = any(
        item.kind == kind and item.bead == target_bead and item.attempt == int(attempt) and item.k == int(k)
        for _bead, item in _lines(beads)
    )
    if not already and not known:
        raise ValueError(f"unknown marker {marker}")
    if not already:
        beads.add_comment(target_bead, f"{prefix} -> {decision}")
    if not any(item.bead == target_bead for _bead, item in _lines(beads)):
        beads.add_label(target_bead, "curated")
    return 0
