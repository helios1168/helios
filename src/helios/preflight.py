"""Preflight checks for ``helios run`` (SPEC §7.1 step 2).

All failures exit 2 before anything is created:

- ``impl`` and ``validate`` need ``files`` and ``test``; verify kinds need
  ``unit`` and ``parent``.
- every name in ``memories`` and every path in ``docs`` exists.
- ``verify-math`` needs a substantive ``## Model``: at least 200 characters
  that are not headings, blank lines or the ``_empty_`` placeholder.
- beads launched together have disjoint ``files`` (glob overlap counts).
- the latest attempt of the bead is finalized, unless recovery (SPEC §8.4)
  applies.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from helios import attempt as attempt_mod
from helios.beads import Bead
from helios.prompt import section

MODEL_MIN_CHARS = 200


class PreflightError(RuntimeError):
    """Preflight failures; the command layer exits 2."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class PreflightContext:
    hub: Path
    runs_rel: str = ".helios/runs"
    units_dir: str = "docs/units"
    memory_has: Callable[[str], bool] = lambda key: True


def check(beads: list[Bead], ctx: PreflightContext) -> list[str]:
    """Return one error string per preflight failure; empty means go."""
    errors: list[str] = []
    for bead in beads:
        errors.extend(_check_bead(bead, ctx))
    errors.extend(_check_disjoint(beads))
    return errors


def ensure(beads: list[Bead], ctx: PreflightContext) -> None:
    """Raise PreflightError when any check fails."""
    errors = check(beads, ctx)
    if errors:
        raise PreflightError(errors)


def _check_bead(bead: Bead, ctx: PreflightContext) -> list[str]:
    errors: list[str] = []
    if bead.kind in ("impl", "validate"):
        if not bead.files:
            errors.append(f"{bead.id}: impl bead needs `files`")
        if not bead.test:
            errors.append(f"{bead.id}: impl bead needs `test`")
    if bead.kind.startswith("verify"):
        if not bead.unit:
            errors.append(f"{bead.id}: verify bead needs `unit`")
        if not bead.parent:
            errors.append(f"{bead.id}: verify bead needs `parent`")
    for key in bead.memories:
        if not ctx.memory_has(key):
            errors.append(f"{bead.id}: memory {key!r} does not exist")
    for entry in bead.docs:
        path_text, _, _ = entry.partition("#")
        if not (ctx.hub / path_text).exists():
            errors.append(f"{bead.id}: docs path {path_text!r} does not exist")
    if bead.kind == "verify-math" and bead.unit:
        errors.extend(_check_model(bead, ctx))
    errors.extend(_check_attempt_finalized(bead, ctx))
    return errors


def _check_model(bead: Bead, ctx: PreflightContext) -> list[str]:
    assert bead.unit is not None
    path = ctx.hub / ctx.units_dir / f"{bead.unit}.md"
    if not path.is_file():
        return [f"{bead.id}: unit file {path} does not exist"]
    try:
        model = section(path.read_text(), "## Model")
    except KeyError:
        return [f"{bead.id}: unit file has no ## Model section"]
    substance = "\n".join(
        line
        for line in (l.strip() for l in model.splitlines())
        if line and not line.startswith("#") and line != "_empty_"
    )
    if len(substance) < MODEL_MIN_CHARS:
        return [f"{bead.id}: ## Model is not substantive (needs 200 characters)"]
    return []


def _check_attempt_finalized(bead: Bead, ctx: PreflightContext) -> list[str]:
    latest = attempt_mod.latest_state(ctx.hub, ctx.runs_rel, bead.id)
    if latest is None:
        return []
    action = attempt_mod.classify_recovery(
        latest.get("state"),
        pid_alive=attempt_mod.is_pid_alive(latest.get("pid")),
    )
    if action == "refuse":
        return [
            f"{bead.id}: attempt {latest.get('attempt_id')} is still running; "
            f"use `helios attach {bead.id}` or `helios stop {bead.id}`"
        ]
    return []


def _check_disjoint(beads: list[Bead]) -> list[str]:
    errors: list[str] = []
    for i, first in enumerate(beads):
        for second in beads[i + 1 :]:
            clash = _first_overlap(first.files, second.files)
            if clash is not None:
                left, right = clash
                errors.append(
                    f"{first.id} and {second.id} overlap in files: {left!r} and {right!r}"
                )
    return errors


def _first_overlap(
    first: list[str], second: list[str]
) -> tuple[str, str] | None:
    for left in first:
        for right in second:
            if globs_overlap(left, right):
                return left, right
    return None


def globs_overlap(left: str, right: str) -> bool:
    """True when two ``files`` entries can cover the same path (SPEC §7.1)."""
    if left == right:
        return True
    for pattern, literal in ((left, right), (right, left)):
        if _covers(pattern, literal):
            return True
    return False


def _covers(pattern: str, other: str) -> bool:
    if pattern.endswith("/"):
        return other == pattern.rstrip("/") or other.startswith(pattern)
    if not any(ch in pattern for ch in "*?["):
        return other == pattern or other.startswith(pattern + "/")
    if fnmatch.fnmatchcase(other, pattern):
        return True
    stem = pattern
    while stem.endswith("/*"):
        stem = stem[:-2]
    if stem != pattern and (other == stem or other.startswith(stem + "/")):
        return True
    return not any(ch in other for ch in "*?[") and fnmatch.fnmatchcase(
        pattern, other
    )


def memory_map(memories: Mapping[str, str]) -> Callable[[str], bool]:
    """Build a ``memory_has`` lookup from an in-memory mapping (tests)."""
    return lambda key: key in memories
