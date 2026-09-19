"""Stage sets (SPEC §3.1).

A stage set is data. A hub declares its stages as ``[[stage]]`` entries in
``.agents/workflow.toml`` and helios reads them; nothing in the dispatch core
names a stage. A hub that declares none gets the research stage set of SPEC
§3.2, shipped beside this module as ``stagesets/research.toml``.

Declaration order is stage order: it sets candidate order (§11) and the order
``helios unit new`` builds a chain in (§10.2).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: What a stage may demand of a bead at preflight (SPEC §7.1 step 2).
REQUIREMENTS: tuple[str, ...] = ("files", "test", "unit", "parent", "model")

#: How §11 judges a finished bead.
GATES: tuple[str, ...] = ("report", "verdict", "none")

#: Which changed paths a bead may own (SPEC §7.4).
OWNERSHIP_MODES: tuple[str, ...] = ("files", "artifacts", "none")

#: The agent spec that asks for a harness differing from the author's (SPEC §5).
OTHER = "other"

_ID_RE = re.compile(r"[a-z][a-z0-9-]*")

_FIELDS = frozenset({"id", "requires", "author", "gate", "ownership", "verifies", "scaffold"})

RESEARCH_PATH = Path(__file__).parent / "stagesets" / "research.toml"


class StageSetError(ValueError):
    """A SPEC §3.1 refusal.

    Subclasses ``ValueError`` so ``config.load`` wraps it as a ``ConfigError``
    and the command layer prints ``helios: `` and exits 2 (SPEC §2.3).
    """


@dataclass(frozen=True)
class StageSpec:
    """One declared stage (SPEC §3.1)."""

    id: str
    author: str
    requires: frozenset[str] = frozenset()
    gate: str = "report"
    ownership: str = "files"
    verifies: str | None = None
    scaffold: bool = True

    def demands(self, requirement: str) -> bool:
        """True when preflight must demand ``requirement`` of a bead of this stage."""
        return requirement in self.requires


@dataclass(frozen=True)
class StageSet:
    """The declared stages, in declaration order."""

    stages: tuple[StageSpec, ...]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(stage.id for stage in self.stages)

    def __contains__(self, stage_id: object) -> bool:
        return any(stage.id == stage_id for stage in self.stages)

    def __iter__(self):
        return iter(self.stages)

    def __len__(self) -> int:
        return len(self.stages)

    def get(self, stage_id: str) -> StageSpec:
        """The stage with this id, or raise ``KeyError`` naming the declared ids."""
        for stage in self.stages:
            if stage.id == stage_id:
                return stage
        raise KeyError(f"{stage_id!r} is not a declared stage; declared: {', '.join(self.ids)}")

    def find(self, stage_id: str) -> StageSpec | None:
        """The stage with this id, or None. For a caller that has its own refusal."""
        try:
            return self.get(stage_id)
        except KeyError:
            return None

    def index(self, stage_id: str) -> int:
        """Declaration index of a stage, which is its order (SPEC §3.1)."""
        for i, stage in enumerate(self.stages):
            if stage.id == stage_id:
                return i
        raise KeyError(f"{stage_id!r} is not a declared stage; declared: {', '.join(self.ids)}")

    @property
    def verify_ids(self) -> frozenset[str]:
        """The stages that check another stage, meaning those declaring ``verifies``."""
        return frozenset(stage.id for stage in self.stages if stage.verifies is not None)

    @property
    def scaffold_ids(self) -> tuple[str, ...]:
        """The stages ``helios unit new`` offers, in declaration order (SPEC §10.2)."""
        return tuple(stage.id for stage in self.stages if stage.scaffold)

    def parent_of(self, stage_id: str) -> str | None:
        """The stage whose bead is the parent of a bead of this stage, else None."""
        return self.get(stage_id).verifies


def parse(tables: list[Any]) -> StageSet:
    """Build a StageSet from parsed ``[[stage]]`` tables, refusing what SPEC §3.1 refuses."""
    if not isinstance(tables, list):
        raise StageSetError("config key 'stage' must be an array of tables")
    if not tables:
        raise StageSetError("config key 'stage' must declare at least one stage")
    stages: list[StageSpec] = []
    seen: set[str] = set()
    for i, table in enumerate(tables):
        stage = _one(table, i)
        if stage.id in seen:
            raise StageSetError(f"stage {stage.id} is declared twice")
        seen.add(stage.id)
        stages.append(stage)
    earlier: set[str] = set()
    for stage in stages:
        if stage.verifies is not None:
            if stage.verifies == stage.id:
                raise StageSetError(f"stage {stage.id} verifies itself")
            if stage.verifies not in seen:
                raise StageSetError(
                    f"stage {stage.id} verifies {stage.verifies}, which is not declared"
                )
            if stage.verifies not in earlier:
                raise StageSetError(
                    f"stage {stage.id} verifies {stage.verifies}, which is not declared earlier"
                )
        elif stage.author == OTHER:
            raise StageSetError(
                f"stage {stage.id} author is {OTHER!r}, which needs a `verifies` to differ from"
            )
        earlier.add(stage.id)
    return StageSet(tuple(stages))


def _one(table: Any, i: int) -> StageSpec:
    if not isinstance(table, dict):
        raise StageSetError(f"stage[{i}] must be a table")
    unknown = sorted(set(table) - _FIELDS)
    if unknown:
        raise StageSetError(f"stage[{i}] has unknown key {unknown[0]!r}")
    stage_id = table.get("id")
    if not isinstance(stage_id, str) or not stage_id:
        raise StageSetError(f"stage[{i}] needs an `id`")
    if not _ID_RE.fullmatch(stage_id):
        raise StageSetError(f"stage {stage_id} id must match [a-z][a-z0-9-]*")
    author = table.get("author")
    if not isinstance(author, str) or not author:
        raise StageSetError(f"stage {stage_id} needs an `author`")
    requires = table.get("requires", [])
    if not isinstance(requires, list) or not all(isinstance(r, str) for r in requires):
        raise StageSetError(f"stage {stage_id} requires must be a list of strings")
    for requirement in requires:
        if requirement not in REQUIREMENTS:
            raise StageSetError(
                f"stage {stage_id} requires {requirement!r}, not one of "
                f"{', '.join(REQUIREMENTS)}"
            )
    gate = table.get("gate", "report")
    if gate not in GATES:
        raise StageSetError(f"stage {stage_id} gate must be one of {', '.join(GATES)}")
    ownership = table.get("ownership", "files")
    if ownership not in OWNERSHIP_MODES:
        raise StageSetError(
            f"stage {stage_id} ownership must be one of {', '.join(OWNERSHIP_MODES)}"
        )
    verifies = table.get("verifies")
    if verifies is not None and (not isinstance(verifies, str) or not verifies):
        raise StageSetError(f"stage {stage_id} verifies must be a stage id")
    scaffold = table.get("scaffold", True)
    if not isinstance(scaffold, bool):
        raise StageSetError(f"stage {stage_id} scaffold must be true or false")
    return StageSpec(
        id=stage_id,
        author=author,
        requires=frozenset(requires),
        gate=gate,
        ownership=ownership,
        verifies=verifies,
        scaffold=scaffold,
    )


def research() -> StageSet:
    """The shipped research stage set of SPEC §3.2, used when a hub declares none."""
    with open(RESEARCH_PATH, "rb") as fh:
        return parse(tomllib.load(fh).get("stage", []))
