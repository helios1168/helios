"""Open gate listing (SPEC section 14)."""

from __future__ import annotations

from typing import Any

from helios.beads import BeadsLike


def open_gates(beads: BeadsLike) -> list[dict[str, Any]]:
    """Return gates and their blocked bead ids in bd order."""
    return [{**gate, "blocks": beads.gate_blocks(str(gate["id"]))} for gate in beads.gate_list()]
