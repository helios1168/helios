"""Learned queue parsing and curation (SPEC section 14)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from helios.beads import Bead, BeadNotFound, BeadsLike
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


def _all_markers(beads: Any, unit: str | None = None) -> dict[str, tuple[Bead, LearnedLine]]:
    """Every distinct marker found on a helios-kind bead's comments, curated or not.

    Keyed by the exact bracketed marker text (``[<kind>:<bead>#<attempt>#<k>]``); the
    first occurrence in bd order wins a duplicate marker. This is the full set
    ``--mark`` validates against (Decided: comparison is on marker text, never on
    parsed numbers, so a leading-zero or Unicode-digit variant of a real marker is
    unknown, and a replay of an already-curated marker still finds it).
    """
    found: dict[str, tuple[Bead, LearnedLine]] = {}
    for bead in _helios_beads(beads):
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
            found[marker] = (bead, LearnedLine(bead_unit, marker_bead, int(attempt), kind, int(k), text))
    return found


def _curated_texts(beads: Any, targets: set[str], *, strict: bool = False) -> list[str]:
    """Every ``curated: [...]`` comment on one of ``targets`` (Decided: a curated
    comment for a marker counts on any bead, so ``targets`` covers every helios-kind
    bead plus every bead a queue marker names, kind label or not).

    A marker can name a bead that no longer exists; ``BeadNotFound`` for that one bead
    is always skipped rather than failing the whole scan (Decided). A bd
    ``RuntimeError`` is skipped too by default (the listing path, ``_lines``), but
    ``strict=True`` (``mark``'s already-curated check) lets it propagate instead of
    being read as "no curated comment" (Decided).
    """
    curated: list[str] = []
    for bead_id in targets:
        try:
            comments = beads.comments(bead_id)
        except BeadNotFound:
            continue
        except RuntimeError:
            if strict:
                raise
            continue
        curated.extend(c.text for c in comments if c.text.startswith("curated: ["))
    return curated


def _lines(beads: Any, unit: str | None = None) -> list[tuple[Bead, LearnedLine]]:
    found = _all_markers(beads, unit)
    targets = {bead.id for bead in _helios_beads(beads)} | {item.bead for _bead, item in found.values()}
    curated = _curated_texts(beads, targets)
    kept = [
        (marker, bead, item)
        for marker, (bead, item) in found.items()
        if not any(c.startswith(f"curated: {marker}") for c in curated)
    ]
    ordered = sorted(
        kept,
        key=lambda triple: (triple[2].unit, triple[2].bead, triple[2].attempt, triple[2].kind, triple[2].k, triple[0]),
    )
    return [(bead, item) for _marker, bead, item in ordered]


def list_lines(beads: Any, unit: str | None = None) -> list[LearnedLine]:
    """Return the curated-filtered learned queue."""
    return [item for _bead, item in _lines(beads, unit)]


def mark(beads: Any, marker: str, decision: str) -> int:
    """Curate one queue entry, replaying an existing mark as a no-op.

    Decided: ``marker`` (``<kind>:<bead>#<attempt>#<k>``) must equal, as text, one of
    the markers of the full listing (curated ones included, so a replay still works);
    an unmatched marker, including a leading-zero or Unicode-digit variant of a real
    one, is unknown.

    When the marker's named bead no longer exists (``beads.show`` raises
    ``BeadNotFound``), the curated comment is written on the source bead instead, the
    bead whose comment carries the marker, and the label step is skipped since there
    is no target bead to label (Decided). The already-curated check catches only
    ``BeadNotFound`` per bead here; a bd ``RuntimeError`` propagates rather than being
    read as "not yet curated" (Decided).
    """
    if decision not in {"memory", "template", "drop"}:
        raise ValueError(f"unknown decision {decision}")
    bracketed = f"[{marker}]"
    found = _all_markers(beads)
    if bracketed not in found:
        raise ValueError(f"unknown marker {marker}")
    source, item = found[bracketed]
    target_bead = item.bead
    prefix = f"curated: {bracketed}"

    try:
        beads.show(target_bead)
        target_exists = True
    except BeadNotFound:
        target_exists = False
    write_bead = target_bead if target_exists else source.id

    targets = {bead.id for bead in _helios_beads(beads)}
    if target_exists:
        targets.add(target_bead)
    already = any(c.startswith(prefix) for c in _curated_texts(beads, targets, strict=True))
    if not already:
        beads.add_comment(write_bead, f"{prefix} -> {decision}")
    if target_exists and not any(item.bead == target_bead for _bead, item in _lines(beads)):
        beads.add_label(target_bead, "curated")
    return 0
