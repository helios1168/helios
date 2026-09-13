"""Preflight checks for ``helios run`` (SPEC §7.1 step 2).

All failures exit 2 before anything is created:

- ``impl`` and ``validate`` need ``files`` and ``test``; verify kinds need
  ``unit`` and ``parent``.
- every name in ``memories`` exists (the memory lookup is a required
  argument); every path in ``docs`` exists, and every ``path#key`` resolves
  to a section (SPEC §7.2).
- ``verify-math`` needs a substantive ``## Model``: the stripped lines that
  are not headings in the SPEC §7.2 sense, not blank and not the ``_empty_``
  placeholder total at least 200 characters; line breaks do not count. A line
  inside a code fence, or without a space after ``#``, counts like any other.
- beads launched together have disjoint ``files``. Two entries overlap when
  either matches the other read as a literal path, or when neither is a
  literal path and the literal prefix of one starts with the literal prefix
  of the other. The literal prefix of a glob is its text before the first
  ``*``, ``?`` or ``[``; of a directory entry, the entry itself. The rule
  over-approximates on purpose.
- the latest attempt of the bead is finalized, unless recovery (SPEC §8.4)
  applies.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from helios import attempt as attempt_mod
from helios.beads import Bead
from helios.ownership import glob_match
from helios.prompt import find_section, heading_lines

MODEL_MIN_CHARS = 200


class PreflightError(RuntimeError):
    """Preflight failures; the command layer exits 2."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class PreflightContext:
    hub: Path
    memory_has: Callable[[str], bool]
    runs_rel: str = ".helios/runs"
    units_dir: str = "docs/units"


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
        errors.extend(_check_doc(bead.id, entry, ctx))
    if bead.kind == "verify-math" and bead.unit:
        errors.extend(_check_model(bead, ctx))
    errors.extend(_check_attempt_finalized(bead, ctx))
    return errors


def _check_doc(bead_id: str, entry: str, ctx: PreflightContext) -> list[str]:
    path_text, sep, key = entry.partition("#")
    path = ctx.hub / path_text
    if not path.exists():
        return [f"{bead_id}: docs path {path_text!r} does not exist"]
    if not path.is_file():
        return [f"{bead_id}: docs path {path_text!r} is a directory, not a file"]
    if sep:
        try:
            find_section(path.read_text(), key)
        except KeyError:
            return [f"{bead_id}: docs key {key!r} does not resolve in {path_text!r}"]
    return []


def _check_model(bead: Bead, ctx: PreflightContext) -> list[str]:
    assert bead.unit is not None
    path = ctx.hub / ctx.units_dir / f"{bead.unit}.md"
    if not path.is_file():
        return [f"{bead.id}: unit file {path} does not exist"]
    try:
        model = find_section(path.read_text(), "Model")
    except KeyError:
        return [f"{bead.id}: unit file has no ## Model section"]
    headings = heading_lines(model)
    substance = sum(
        len(stripped)
        for n, line in enumerate(model.splitlines())
        if (stripped := line.strip()) and n not in headings and stripped != "_empty_"
    )
    if substance < MODEL_MIN_CHARS:
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


def _is_literal_path(entry: str) -> bool:
    return not entry.endswith("/") and not any(ch in entry for ch in "*?[")


def _literal_prefix(entry: str) -> str:
    if entry.endswith("/"):
        return entry
    for i, char in enumerate(entry):
        if char in "*?[":
            return entry[:i]
    return entry


def _entry_matches_literal(entry: str, literal: str) -> bool:
    if entry.endswith("/"):
        base = entry.rstrip("/")
        return literal == base or literal.startswith(entry)
    if _is_literal_path(entry):
        return literal == entry or literal.startswith(entry + "/")
    return glob_match(literal, entry)


def globs_overlap(left: str, right: str) -> bool:
    """True when two ``files`` entries can cover the same path (SPEC §7.1)."""
    if _entry_matches_literal(left, right) or _entry_matches_literal(right, left):
        return True
    if _is_literal_path(left) or _is_literal_path(right):
        return False
    return _literal_prefix(left).startswith(_literal_prefix(right)) or _literal_prefix(
        right
    ).startswith(_literal_prefix(left))


def memory_map(memories: Mapping[str, str]) -> Callable[[str], bool]:
    """Build a ``memory_has`` lookup from an in-memory mapping (tests)."""
    return lambda key: key in memories
