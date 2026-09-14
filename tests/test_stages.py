"""Stage ids and parent-stage lookup tests (SPEC §3, §10.2)."""

from __future__ import annotations

import re
from pathlib import Path

from helios.stages import STAGES, VERIFY_STAGES, parent_stage

SPEC = Path(__file__).resolve().parents[1] / "docs" / "SPEC.md"


def _spec_stage_ids() -> list[str]:
    """Parse the `id` column of the §3 Stages table in docs/SPEC.md, in row order."""
    text = SPEC.read_text()
    section = text.split("## 3. Stages", 1)[1].split("\n## ", 1)[0]
    ids = []
    for line in section.splitlines():
        match = re.match(r"\|\s*`([a-z-]+)`\s*\|", line)
        if match:
            ids.append(match.group(1))
    return ids


def test_stages_match_spec_table() -> None:
    assert list(STAGES) == _spec_stage_ids()


def test_verify_stages_are_the_three_verify_ids() -> None:
    assert VERIFY_STAGES == {"verify-math", "verify-code", "verify-validate"}
    assert VERIFY_STAGES <= set(STAGES)


def test_parent_stage_skips_verify_stages() -> None:
    stages = ["model", "verify-math", "impl", "verify-code"]
    assert parent_stage(stages, 1) == 0
    assert parent_stage(stages, 3) == 2


def test_parent_stage_walks_back_over_consecutive_verify_stages() -> None:
    stages = ["impl", "verify-code", "verify-code"]
    assert parent_stage(stages, 2) == 0


def test_parent_stage_returns_none_at_start() -> None:
    assert parent_stage(["frame"], 0) is None
    assert parent_stage(["verify-math"], 0) is None
