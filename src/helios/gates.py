"""Open gate listing (SPEC section 14)."""

from __future__ import annotations

from typing import Any

from helios.beads import BeadsLike


class GateError(Exception):
    """bd gate output is not a list of gate objects with a string id."""


def open_gates(beads: BeadsLike) -> list[dict[str, Any]]:
    """Return gates and their blocked bead ids in bd order (SPEC section 14).

    Raises ``GateError`` on bd gate output that is not a list, or an entry without a
    string ``id``; the command layer turns that into ``helios: unexpected bd gate
    output`` and exit 1 (Decided: distinct from a config-loading error, exit 2).
    """
    gates = beads.gate_list()
    if not isinstance(gates, list):
        raise GateError("unexpected bd gate output")
    result: list[dict[str, Any]] = []
    for gate in gates:
        if not isinstance(gate, dict) or not isinstance(gate.get("id"), str):
            raise GateError("unexpected bd gate output")
        result.append({**gate, "blocks": beads.gate_blocks(gate["id"])})
    return result
