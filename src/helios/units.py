"""Unit chains: ``helios unit new`` (SPEC §10.2).

Validation (steps 1 and 3) runs before any bd write: every failure raises
``UnitNewError``, which the command prints with a ``helios: `` prefix and
exits 2. Beads are looked up for every stage first, so an ambiguous reuse
refuses before any bead is created; missing beads are then created with the
step 3 author, chained with ``dep_add`` plus the verify ``parent`` metadata
(step 4), and the unit file is written last with ``open(path, "x")`` so a
crash leaves no unit file and a rerun reuses the beads (step 5). The caller
prints ``format_table`` (step 6). The command holds ``unit_lock`` from before
step 1 until it exits.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import re
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from helios.beads import Bead
from helios.config import Config, resolve_harness
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

_UNIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_PLACEHOLDER_RE = re.compile(r"\{(unit|title|stages)\}")


class UnitNewError(Exception):
    """A SPEC §10.2 refusal; the command prints it with ``helios: `` and exits 2."""


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


@contextlib.contextmanager
def unit_lock(hub: Path, runs_dir: str, unit: str) -> Iterator[None]:
    """Hold the exclusive non-blocking creation lock (SPEC §10.2).

    The caller validates the unit id first, so the lock path never uses an
    unvalidated id. A lock file that cannot be created or locked for any
    reason other than "held" refuses; the lock is never skipped.
    """
    path = hub / runs_dir / f"unit-{unit}.lock"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a")
    except OSError as exc:
        raise UnitNewError(f"cannot lock {path}: {_error_text(exc)}") from None
    with handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES):
                raise UnitNewError(
                    f"unit {unit} is being created by another process"
                ) from None
            raise UnitNewError(f"cannot lock {path}: {_error_text(exc)}") from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _error_text(exc: OSError) -> str:
    """Human text for a lock OSError: the strerror when it has one."""
    return exc.strerror or str(exc)


def validate_unit_id(unit: str) -> None:
    """Refuse a unit id outside `^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$` (SPEC §10.2)."""
    if re.fullmatch(_UNIT_RE, unit) is None:
        raise UnitNewError(
            f"invalid unit name {unit!r}: must match ^[A-Za-z0-9][A-Za-z0-9._-]{{0,99}}$"
        )


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
    *, spec: str, parent_author: str, config: Config
) -> str:
    """Resolve an ``other`` spec against the parent author (SPEC §5, §10.2 step 3).

    The recorded author is the harness name. An unresolvable ``other`` is a
    refusal with the SPEC step 3 message.
    """
    try:
        return resolve_harness(
            spec, author=parent_author, verify_order=config.agents.verify_order
        )
    except ValueError:
        harness = parent_author.partition(":")[0]
        raise UnitNewError(
            f"no harness in agents.verify_order differs from {harness}"
        ) from None


def _recorded_author(bead: Bead) -> str | None:
    """The bead's recorded author metadata, None when missing or empty."""
    author = bead.author or bead.metadata.get("author")
    return author if isinstance(author, str) and author else None


def _check_units_dir(*, hub: Path, units: str) -> None:
    """Refuse when an existing units-dir component is not a directory.

    SPEC §10.2 step 1, checked before anything is written. Each component is
    examined with ``os.lstat`` so symlinks are seen for what they are: a
    symlink must resolve (``os.stat`` succeeds) to a directory.
    """
    node: Path | None = None
    for part in (hub / units).parts:
        node = Path(part) if node is None else node / part
        try:
            kind = os.lstat(node)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise UnitNewError(
                f"cannot stat units directory component {node}: {_error_text(exc)}"
            ) from None
        if stat.S_ISLNK(kind.st_mode):
            try:
                target = os.stat(node)
            except OSError:
                raise UnitNewError(
                    f"units directory component is a broken symlink: {node}"
                ) from None
            if not stat.S_ISDIR(target.st_mode):
                raise UnitNewError(
                    f"units directory component is not a directory: {node}"
                )
        elif not stat.S_ISDIR(kind.st_mode):
            raise UnitNewError(
                f"units directory component is not a directory: {node}"
            )


def _validate(
    *,
    unit: str,
    stages_raw: str,
    files_raw: str | None,
    test: str | None,
    hub: Path,
    units: str,
    unit_path: Path,
) -> tuple[list[str], list[str] | None]:
    """Check every SPEC §10.2 step 1 rule before anything is written."""
    validate_unit_id(unit)
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
        if test is None or test.strip() == "":
            raise UnitNewError("--test is required when impl or validate is present")
    _check_units_dir(hub=hub, units=units)
    if os.path.lexists(unit_path):
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

    Raises ``UnitNewError`` before any bd write when steps 1 to 3 refuse. The
    caller holds ``unit_lock``.
    """
    units_dir = config.project.units
    unit_path = config.hub / units_dir / f"{unit}.md"
    stage_ids, file_list = _validate(
        unit=unit,
        stages_raw=stages,
        files_raw=files,
        test=test,
        hub=config.hub,
        units=units_dir,
        unit_path=unit_path,
    )
    reused: list[Bead | None] = []
    for stage in stage_ids:
        candidates = [
            bead
            for bead in beads.list(labels=[f"unit:{unit}", f"kind:{stage}"])
            if bead.status != "closed"
        ]
        if len(candidates) > 1:
            names = ", ".join(bead.id for bead in candidates)
            raise UnitNewError(
                f"more than one open bead for stage {stage}: {names}"
            )
        reused.append(candidates[0] if candidates else None)
    authors: list[str] = []
    for index, stage in enumerate(stage_ids):
        spec = _spec_for_stage(config, stage)
        if stage in _VERIFY_STAGES:
            if spec == "other":
                parent = _parent_index(stage_ids, index)
                assert parent is not None  # refused by _validate
                parent_bead = reused[parent]
                if parent_bead is not None:
                    recorded = _recorded_author(parent_bead)
                    if recorded is None or recorded.strip() == "":
                        raise UnitNewError(
                            f"parent bead {parent_bead.id} has no usable author"
                        )
                    parent_author = recorded
                else:
                    parent_author = authors[parent]
                authors.append(
                    _resolve_author(
                        spec=spec, parent_author=parent_author, config=config
                    )
                )
            else:
                authors.append(spec)
        else:
            if spec == "other":
                raise UnitNewError(
                    f"agent spec other is valid only for verify stages, not {stage}"
                )
            authors.append(spec)
    bead_ids: list[str] = []
    for index, (stage, author) in enumerate(zip(stage_ids, authors)):
        hit = reused[index]
        if hit is not None:
            bead_id = hit.id
            if stage in _VERIFY_STAGES:
                parent = _parent_index(stage_ids, index)
                assert parent is not None  # refused by _validate
                if hit.parent != bead_ids[parent]:
                    beads.set_metadata(bead_id, {"parent": bead_ids[parent]})
        else:
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
    rows: list[StageRow] = []
    for i, (bead_id, stage) in enumerate(zip(bead_ids, stage_ids)):
        hit = reused[i]
        recorded = _recorded_author(hit) if hit is not None else None
        rows.append(
            StageRow(
                bead=bead_id,
                stage=stage,
                author=recorded or authors[i],
                blocked_by=bead_ids[i - 1] if i else None,
            )
        )
    return rows


def format_table(rows: list[StageRow]) -> str:
    """The tab-separated step 6 table, header plus one row per stage."""
    lines = ["bead\tstage\tauthor\tblocked-by"]
    for row in rows:
        lines.append(
            f"{row.bead}\t{row.stage}\t{row.author}\t{row.blocked_by or '-'}"
        )
    return "\n".join(lines) + "\n"
