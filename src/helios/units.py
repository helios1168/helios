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

from helios import harness, stageset
from helios.beads import Bead
from helios.config import Config, resolve_harness, spec_for_kind
from helios.templates import path as template_path

_UNIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_PLACEHOLDER_RE = re.compile(r"\{(unit|title|stages)\}")


class UnitNewError(Exception):
    """A SPEC §10.2 refusal; the command prints it with ``helios: `` and exits 2."""


class UnitWriteError(Exception):
    """A step 5 failure after beads exist (round 3 decision); the command
    prints it with ``helios: `` and exits 1. The beads already created stay;
    a rerun follows the normal step 5 rerun rule.
    """


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
    reason other than "held" refuses; the lock is never skipped. The path is
    opened with ``O_NOFOLLOW`` (round 3 decision: a symlink, including a
    dangling one, refuses rather than opening its target) and the open file
    must be a regular file, so a FIFO does not hang the open and a directory
    or device does not pass as a lock.
    """
    path = hub / runs_dir / f"unit-{unit}.lock"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644
        )
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EISDIR):
            raise UnitNewError(f"cannot lock {path}: not a regular file") from None
        raise UnitNewError(f"cannot lock {path}: {_error_text(exc)}") from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise UnitNewError(f"cannot lock {path}: not a regular file")
    handle = os.fdopen(fd, "r+")
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


def _parent_index(config: Config, requested: list[str], index: int) -> int | None:
    """Index within the requested ``--stages`` list of the stage this one verifies.

    The parent is ``StageSet.parent_of`` the stage at ``index``; it counts only
    when it also appears earlier in the requested list (SPEC §10.2 step 4).
    """
    parent_id = config.stages.parent_of(requested[index])
    if parent_id is None:
        return None
    for j in range(index - 1, -1, -1):
        if requested[j] == parent_id:
            return j
    return None


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
        harness_name = parent_author.partition(":")[0]
        raise UnitNewError(
            f"no harness in agents.verify_order differs from {harness_name}"
        ) from None


def _recorded_author(bead: Bead) -> str | None:
    """The bead's recorded ``author``, from ``.author`` or the raw metadata.

    ``Bead.author`` already carries a from-JSON bead's author as text; a bead
    built directly with only ``metadata`` set (as tests do) falls back to the
    metadata dict. Only a string value counts as recorded.
    """
    if bead.author:
        return bead.author
    value = bead.metadata.get("author")
    return value if isinstance(value, str) else None


def _usable_author(recorded: str) -> bool:
    """True when ``recorded``'s harness, before any ``:``, is one the adapter
    registry knows (SPEC §10.2 step 3). ``harness.get`` is the registry's only
    lookup, so asking it is what "the registry knows a harness" means; a
    harness added there later needs no matching change here.
    """
    try:
        harness.get(recorded.partition(":")[0])
    except ValueError:
        return False
    return True


def _display_author(bead: Bead) -> str:
    """A reused bead's row author: its recorded author, or ``-`` when it has
    none (round 3 decision: missing, empty or whitespace-only counts as none;
    a recorded but unusable value such as ``bogus`` still displays as is).
    """
    recorded = _recorded_author(bead)
    return "-" if recorded is None or recorded.strip() == "" else recorded


def _check_units_writable(*, hub: Path, units: str) -> None:
    """Refuse when the deepest existing units-dir component is not writable.

    Round 3 decision: ``os.access(<deepest existing component>, os.W_OK |
    os.X_OK)``, checked before any bd write.
    """
    node = hub
    for part in Path(units).parts:
        candidate = node / part
        if not os.path.lexists(candidate):
            break
        node = candidate
    if not os.access(node, os.W_OK | os.X_OK):
        raise UnitNewError(f"units directory is not writable: {node}")


def _check_hub_containment(*, hub: Path, units: str) -> None:
    """Refuse when the units directory resolves outside the hub.

    Round 3 decision: symlinks are followed with ``os.path.realpath``; the
    resolved path must lie inside the resolved hub. Checked in step 1 and
    again in step 5, immediately before writing, to close the TOCTOU window
    where the units path changes while beads are created.
    """
    hub_real = os.path.realpath(hub)
    units_real = os.path.realpath(hub / units)
    if units_real != hub_real and not units_real.startswith(hub_real + os.sep):
        raise UnitNewError(f"units directory resolves outside the hub: {hub / units}")


def _check_paths(*, hub: Path, units: str, unit_path: Path) -> None:
    """All SPEC §10.2 step 1 path checks; step 5 repeats these before writing
    (round 3 decision), so the units directory and unit file are re-checked
    against the filesystem as it is right before the write, not as it was at
    step 1.
    """
    _check_units_dir(hub=hub, units=units)
    _check_units_writable(hub=hub, units=units)
    _check_hub_containment(hub=hub, units=units)
    if os.path.lexists(unit_path):
        raise UnitNewError(f"unit file already exists: {unit_path}")


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
    config: Config,
) -> tuple[list[str], list[str] | None]:
    """Check every SPEC §10.2 step 1 rule before anything is written."""
    validate_unit_id(unit)
    stages = stages_raw.split(",")
    if any(item == "" for item in stages):
        raise UnitNewError("--stages has an empty item")
    if len(set(stages)) != len(stages):
        raise UnitNewError("--stages has duplicate items")
    declared = config.stages
    for stage in stages:
        spec = declared.find(stage)
        if spec is None:
            raise UnitNewError(
                f"unknown stage {stage!r}; declared: {', '.join(declared.ids)}"
            )
        if not spec.scaffold:
            raise UnitNewError(f"stage {stage} cannot be scaffolded")
    for earlier, later in zip(stages, stages[1:]):
        if declared.index(later) <= declared.index(earlier):
            raise UnitNewError(
                f"--stages are not in stage order: {later!r} comes after {earlier!r}"
            )
    for index, stage in enumerate(stages):
        if stage in declared.verify_ids and _parent_index(config, stages, index) is None:
            raise UnitNewError(f"{stage} has no earlier stage to verify")
    files: list[str] | None = None
    if any(declared.get(stage).demands("files") for stage in stages):
        if files_raw is None:
            raise UnitNewError("--files is required when a listed stage requires files")
        files = files_raw.split(",")
        if any(item == "" for item in files):
            raise UnitNewError("--files has an empty item")
    if any(declared.get(stage).demands("test") for stage in stages):
        if test is None or test.strip() == "":
            raise UnitNewError("--test is required when a listed stage requires test")
    _check_paths(hub=hub, units=units, unit_path=unit_path)
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
        config=config,
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
    verify_ids = config.stages.verify_ids
    authors: list[str] = []
    for index, stage in enumerate(stage_ids):
        spec = spec_for_kind(config, stage)
        if stage in verify_ids:
            if spec == stageset.OTHER:
                parent = _parent_index(config, stage_ids, index)
                assert parent is not None  # refused by _validate
                parent_bead = reused[parent]
                if parent_bead is not None:
                    recorded = _recorded_author(parent_bead)
                    if recorded is None or not _usable_author(recorded):
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
            if spec == stageset.OTHER:
                raise UnitNewError(
                    f"agent spec other is valid only for a stage that verifies "
                    f"another stage, not {stage}"
                )
            authors.append(spec)
    bead_ids: list[str] = []
    for index, (stage, author) in enumerate(zip(stage_ids, authors)):
        hit = reused[index]
        if hit is not None:
            bead_id = hit.id
            if stage in verify_ids:
                parent = _parent_index(config, stage_ids, index)
                assert parent is not None  # refused by _validate
                if hit.parent != bead_ids[parent]:
                    beads.set_metadata(bead_id, {"parent": bead_ids[parent]})
        else:
            stage_spec = config.stages.get(stage)
            metadata: dict[str, Any] = {"unit": unit, "kind": stage, "author": author}
            if stage_spec.demands("files"):
                assert file_list is not None  # required by _validate
                metadata["files"] = file_list
            if stage_spec.demands("test"):
                metadata["test"] = test
            if stage in verify_ids:
                parent = _parent_index(config, stage_ids, index)
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
    try:
        _check_paths(hub=config.hub, units=units_dir, unit_path=unit_path)
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(unit_path, "x", encoding="utf-8") as handle:
            handle.write(text)
    except UnitNewError as exc:
        raise UnitWriteError(f"cannot write unit file {unit_path}: {exc}") from None
    except OSError as exc:
        raise UnitWriteError(
            f"cannot write unit file {unit_path}: {_error_text(exc)}"
        ) from None
    rows: list[StageRow] = []
    for i, (bead_id, stage) in enumerate(zip(bead_ids, stage_ids)):
        hit = reused[i]
        author_display = _display_author(hit) if hit is not None else authors[i]
        rows.append(
            StageRow(
                bead=bead_id,
                stage=stage,
                author=author_display,
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
