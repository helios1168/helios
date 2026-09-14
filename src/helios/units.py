"""Unit chains: ``helios unit new`` (SPEC §10).

Validation (step 1) runs before anything is written: every failure raises
``UnitNewError``. Beads are created or reused (step 2) with the author of
step 3, chained with ``dep_add`` plus the verify ``parent`` metadata
(step 4), and the unit file is written last with ``open(path, "x")`` so a
crash leaves no unit file and a rerun reuses the beads (step 5). The caller
prints ``format_table`` (step 6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from helios.beads import Bead
from helios.config import Config, resolve_harness, split_spec
from helios.templates import path as template_path

STAGE_ORDER: tuple[str, ...] = (
    "frame",
    "survey",
    "model",
    "verify-math",
    "impl",
    "verify-code",
    "validate",
    "verify-validate",
    "report",
)
"""Row order of the SPEC §3 table, without ``remember`` (it gets no bead)."""

_VERIFY_STAGES: frozenset[str] = frozenset(
    {"verify-math", "verify-code", "verify-validate"}
)
_NEEDS_FILES: frozenset[str] = frozenset({"impl", "validate"})
_VERIFY_AGENT_KEY: dict[str, str] = {
    "verify-math": "verify_math",
    "verify-code": "verify_code",
    "verify-validate": "verify_validate",
}

_UNIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_PLACEHOLDER_RE = re.compile(r"\{(unit|title|stages)\}")


class UnitNewError(Exception):
    """A SPEC §10.2 step 1 validation refusal; the command exits 2."""


class UnitBeads(Protocol):
    """The ``helios.beads`` calls ``unit new`` needs (SPEC §10.2)."""

    def create(
        self,
        title: str,
        *,
        labels: list[str],
        metadata: dict[str, Any],
        type: str,
        description: str,
    ) -> str: ...
    def dep_add(self, blocked: str, blocker: str) -> None: ...
    def list(self, *, labels: list[str], status: str | None = None) -> list[Bead]: ...
    def set_metadata(self, bead_id: str, metadata: dict[str, str]) -> None: ...


@dataclass(frozen=True)
class StageRow:
    """One row of the SPEC §10.2 step 6 table."""

    bead: str
    stage: str
    author: str
    blocked_by: str | None


def _parent_index(stages: list[str], index: int) -> int | None:
    """Index of the nearest earlier non-verify stage (SPEC §10.2 step 4)."""
    for j in range(index - 1, -1, -1):
        if stages[j] not in _VERIFY_STAGES:
            return j
    return None


def _spec_for_stage(config: Config, stage: str) -> str:
    """The configured agent spec for a stage (SPEC §10.2 step 3)."""
    agents = config.agents
    if stage in ("frame", "survey", "report"):
        return agents.orchestrate
    if stage == "model":
        return agents.model
    if stage in ("impl", "validate"):
        return agents.implement
    return getattr(agents, _VERIFY_AGENT_KEY[stage])


def _resolve_author(
    *, spec: str, parent_author: str | None, config: Config
) -> str:
    """Resolve an agent spec to the recorded author (SPEC §5, §10.2 step 3).

    A plain spec is recorded as is. ``other`` resolves against the parent
    stage author, and the recorded author is the harness name.
    """
    if split_spec(spec)[0] != "other":
        return spec
    return resolve_harness(
        spec, author=parent_author, verify_order=config.agents.verify_order
    )


def _validate(
    *,
    unit: str,
    stages_raw: str,
    files_raw: str | None,
    test: str | None,
    unit_path: Path,
) -> tuple[list[str], list[str] | None]:
    """Check every SPEC §10.2 step 1 rule before anything is written."""
    if not _UNIT_RE.match(unit):
        raise UnitNewError(
            f"invalid unit name {unit!r}: must match ^[A-Za-z0-9][A-Za-z0-9._-]*$"
        )
    stages = stages_raw.split(",")
    if any(item == "" for item in stages):
        raise UnitNewError("--stages has an empty item")
    if len(set(stages)) != len(stages):
        raise UnitNewError("--stages has duplicate items")
    order = {stage: index for index, stage in enumerate(STAGE_ORDER)}
    for stage in stages:
        if stage == "remember":
            raise UnitNewError(
                '"remember" never blocks so it gets no bead (SPEC §10.2 step 1)'
            )
        if stage not in order:
            raise UnitNewError(f"unknown stage {stage!r}")
    for earlier, later in zip(stages, stages[1:]):
        if order[later] <= order[earlier]:
            raise UnitNewError(
                f"--stages are not in stage order: {later!r} comes after {earlier!r}"
            )
    for index, stage in enumerate(stages):
        if stage in _VERIFY_STAGES and _parent_index(stages, index) is None:
            raise UnitNewError(f"{stage} has no earlier stage to verify")
    files: list[str] | None = None
    if any(stage in _NEEDS_FILES for stage in stages):
        if files_raw is None:
            raise UnitNewError("--files is required when impl or validate is present")
        files = files_raw.split(",")
        if any(item == "" for item in files):
            raise UnitNewError("--files has an empty item")
        if not test:
            raise UnitNewError("--test is required when impl or validate is present")
    if unit_path.exists():
        raise UnitNewError(f"unit file already exists: {unit_path}")
    return stages, files


def _render_template(*, unit: str, title: str, stages: list[str]) -> str:
    """Render ``templates/unit.md`` in one ``re.sub`` pass (SPEC §10.2 step 5)."""
    template = template_path("unit.md").read_text(encoding="utf-8")
    values = {"unit": unit, "title": title, "stages": ",".join(stages)}
    return _PLACEHOLDER_RE.sub(lambda match: values[match.group(1)], template)


def create_unit(
    *,
    beads: UnitBeads,
    config: Config,
    unit: str,
    title: str,
    stages: str,
    files: str | None,
    test: str | None,
) -> list[StageRow]:
    """Run ``helios unit new`` (SPEC §10.2); return one row per stage.

    Raises ``UnitNewError`` before writing anything when step 1 refuses.
    """
    unit_path = config.hub / config.project.units / f"{unit}.md"
    stage_ids, file_list = _validate(
        unit=unit,
        stages_raw=stages,
        files_raw=files,
        test=test,
        unit_path=unit_path,
    )
    authors: list[str] = []
    for index, stage in enumerate(stage_ids):
        parent_author: str | None = None
        if stage in _VERIFY_STAGES:
            parent = _parent_index(stage_ids, index)
            assert parent is not None  # refused by _validate
            parent_author = authors[parent]
        authors.append(
            _resolve_author(
                spec=_spec_for_stage(config, stage),
                parent_author=parent_author,
                config=config,
            )
        )
    bead_ids: list[str] = []
    for index, (stage, author) in enumerate(zip(stage_ids, authors)):
        candidates = beads.list(labels=[f"unit:{unit}", f"kind:{stage}"])
        reused = next((b for b in candidates if b.status != "closed"), None)
        if reused is None:
            metadata: dict[str, Any] = {"unit": unit, "kind": stage, "author": author}
            if stage in _NEEDS_FILES:
                assert file_list is not None  # required by _validate
                metadata["files"] = file_list
                metadata["test"] = test
            if stage in _VERIFY_STAGES:
                parent = _parent_index(stage_ids, index)
                assert parent is not None  # refused by _validate
                metadata["parent"] = bead_ids[parent]
            bead_id = beads.create(
                f"{unit} {stage}: {title}",
                labels=[f"unit:{unit}", f"kind:{stage}"],
                metadata=metadata,
                type="task",
                description="",
            )
        else:
            bead_id = reused.id
            if stage in _VERIFY_STAGES:
                parent = _parent_index(stage_ids, index)
                assert parent is not None  # refused by _validate
                if reused.parent != bead_ids[parent]:
                    beads.set_metadata(bead_id, {"parent": bead_ids[parent]})
        bead_ids.append(bead_id)
    for later, earlier in zip(bead_ids[1:], bead_ids[:-1]):
        # bd treats adding an existing dependency as a no-op (see
        # helios.beads.Beads.dep_add), so replaying after a crash in this
        # loop adds each edge once in effect.
        beads.dep_add(later, earlier)
    text = _render_template(unit=unit, title=title, stages=stage_ids)
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(unit_path, "x", encoding="utf-8") as handle:
            handle.write(text)
    except FileExistsError:
        raise UnitNewError(f"unit file already exists: {unit_path}") from None
    return [
        StageRow(
            bead=bead_id,
            stage=stage,
            author=author,
            blocked_by=bead_ids[i - 1] if i else None,
        )
        for i, (bead_id, stage, author) in enumerate(zip(bead_ids, stage_ids, authors))
    ]


def format_table(rows: list[StageRow]) -> str:
    """The tab-separated step 6 table, header plus one row per stage."""
    lines = ["bead\tstage\tauthor\tblocked-by"]
    for row in rows:
        lines.append(
            f"{row.bead}\t{row.stage}\t{row.author}\t{row.blocked_by or '-'}"
        )
    return "\n".join(lines) + "\n"
