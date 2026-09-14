"""Stage ids and parent-stage lookup (SPEC §3, §10.2)."""

from __future__ import annotations

from typing import Sequence

STAGES: tuple[str, ...] = (
    "frame",
    "survey",
    "model",
    "verify-math",
    "impl",
    "verify-code",
    "validate",
    "verify-validate",
    "report",
    "remember",
)

VERIFY_STAGES: frozenset[str] = frozenset({"verify-math", "verify-code", "verify-validate"})


def parent_stage(stages: Sequence[str], index: int) -> int | None:
    """Index of the nearest earlier non-verify stage before ``index`` (SPEC §10.2).

    Used to compute a verify bead's ``parent`` and to resolve the ``other`` role
    against the author of the stage it verifies. Returns ``None`` when every
    earlier item is a verify stage or ``index`` is 0.
    """
    for i in range(index - 1, -1, -1):
        if stages[i] not in VERIFY_STAGES:
            return i
    return None
