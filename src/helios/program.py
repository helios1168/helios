"""Program registry and program command library (SPEC §15.1, §15.3)."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

KINDS: tuple[str, ...] = (
    "set",
    "parameter",
    "variable",
    "definition",
    "objective",
    "constraint",
    "stage",
)
"""Kind order of SPEC §15.1; also the section order of `program show`."""

CHECKED_KINDS: tuple[str, ...] = ("constraint", "objective")
"""Kinds whose recorded build names `program check` compares (SPEC §15.3)."""

OMIT_ENV = "HELIOS_OMIT"


@dataclass
class Block:
    """One program block (SPEC §15.1)."""

    id: str
    kind: str
    expr: Any
    build: Callable[..., Any] | None = None
    satisfies: tuple[str, ...] = ()
    relaxes: tuple[str, ...] = ()
    since: str | None = None
    replaces: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown block kind {self.kind!r}")
        self.satisfies = tuple(self.satisfies)
        self.relaxes = tuple(self.relaxes)


def omit_ids() -> set[str]:
    """Ids listed in HELIOS_OMIT, comma-separated (SPEC §15.1)."""
    return {part.strip() for part in os.environ.get(OMIT_ENV, "").split(",") if part.strip()}


class Registry:
    """Active and retired blocks; ids are unique and never reused (SPEC §15.1)."""

    def __init__(self) -> None:
        self._active: dict[str, Block] = {}
        self._retired: dict[str, Block] = {}
        self._seen: set[str] = set()

    def add(self, block: Block) -> None:
        """Add an active block; ValueError when the id was ever used."""
        if block.id in self._seen:
            raise ValueError(f"block id {block.id!r} was already used")
        self._seen.add(block.id)
        self._active[block.id] = block

    def retire(self, id: str) -> None:
        """Move an active block to retired; KeyError when not active."""
        if id not in self._active:
            raise KeyError(id)
        self._retired[id] = self._active.pop(id)

    def active(self) -> list[Block]:
        """Active blocks in insertion order, minus HELIOS_OMIT ids."""
        omitted = omit_ids()
        return [b for i, b in self._active.items() if i not in omitted]

    def retired(self) -> list[Block]:
        """Retired blocks in insertion order."""
        return list(self._retired.values())


def ensure_hub_path(hub: Path) -> None:
    """Put the hub and <hub>/src first on sys.path (SPEC §15.2 step 3)."""
    for path in (str(hub), str(hub / "src")):
        if path in sys.path:
            sys.path.remove(path)
    sys.path.insert(0, str(hub))
    sys.path.insert(0, str(hub / "src"))


def load_module(name: str, hub: Path) -> ModuleType:
    """Import a dotted module from the hub, freshly (SPEC §15.2 step 3)."""
    ensure_hub_path(hub)
    sys.modules.pop(name, None)
    importlib.invalidate_caches()
    return importlib.import_module(name)


def get_registry(module: ModuleType) -> Registry:
    """Return the module REGISTRY, raising ValueError/TypeError when absent."""
    try:
        registry = module.REGISTRY
    except AttributeError:
        raise ValueError(f"program module {module.__name__!r} has no REGISTRY") from None
    if not isinstance(registry, Registry):
        raise TypeError(f"program module {module.__name__!r} REGISTRY is not a Registry")
    return registry


def load_registry(hub: Path, module_name: str | None) -> tuple[ModuleType, Registry]:
    """Import the program module and return it with its registry."""
    if not module_name:
        raise ValueError("project.program is not configured")
    module = load_module(module_name, hub)
    return module, get_registry(module)


def format_block(block: Block) -> str:
    """One `program show` line (SPEC §15.3)."""
    expr = str(block.expr).replace("\n", " ")
    halves = []
    if block.satisfies:
        halves.append("satisfies " + ", ".join(block.satisfies))
    if block.relaxes:
        halves.append("relaxes " + ", ".join(block.relaxes))
    line = f"{block.id}  {expr}"
    if halves:
        line += "  # " + " / ".join(halves)
    return line


def show_text(registry: Registry) -> str:
    """Deterministic markdown for the registry (SPEC §15.3)."""
    lines: list[str] = []
    by_kind: dict[str, list[Block]] = {kind: [] for kind in KINDS}
    for block in registry.active():
        by_kind[block.kind].append(block)
    for kind in KINDS:
        blocks = sorted(by_kind[kind], key=lambda b: b.id)
        if not blocks:
            continue
        lines.append(f"## {kind}")
        lines.extend(format_block(b) for b in blocks)
    retired = sorted(registry.retired(), key=lambda b: b.id)
    if retired:
        lines.append("## Retired")
        lines.extend(format_block(b) for b in retired)
    return "".join(line + "\n" for line in lines)


class _Recorder:
    """Records kwargs["name"] when it is a string; every step returns a recorder."""

    __slots__ = ("_sink", "_kind")

    def __init__(self, sink: list[tuple[str, str]], kind: list[str]) -> None:
        object.__setattr__(self, "_sink", sink)
        object.__setattr__(self, "_kind", kind)

    def __getattr__(self, item: str) -> _Recorder:
        return _Recorder(self._sink, self._kind)

    def __call__(self, *args: Any, **kwargs: Any) -> _Recorder:
        name = kwargs.get("name")
        if isinstance(name, str):
            self._sink.append((self._kind[0], name))
        return _Recorder(self._sink, self._kind)


class _RecordingModel:
    """Returns a recorder for any attribute (SPEC §15.3)."""

    __slots__ = ("_sink", "_kind")

    def __init__(self, sink: list[tuple[str, str]], kind: list[str]) -> None:
        object.__setattr__(self, "_sink", sink)
        object.__setattr__(self, "_kind", kind)

    def __getattr__(self, item: str) -> _Recorder:
        return _Recorder(self._sink, self._kind)


def check_registry(registry: Registry, data: Any) -> tuple[list[str], list[str]]:
    """Build active blocks on a recording model; return (missing, unexpected) (SPEC §15.3)."""
    sink: list[tuple[str, str]] = []
    kind: list[str] = [""]
    model = _RecordingModel(sink, kind)
    for block in registry.active():
        if block.build is None:
            continue
        kind[0] = block.kind
        block.build(model, data)
    recorded = {name.split("[", 1)[0] for k, name in sink if k in CHECKED_KINDS}
    expected = {
        b.id for b in registry.active() if b.kind in CHECKED_KINDS and b.build is not None
    }
    return sorted(expected - recorded), sorted(recorded - expected)


def module_paths(module_name: str) -> list[str]:
    """Registry module paths at a git revision, src/ first (SPEC §15.3)."""
    rel = module_name.replace(".", "/") + ".py"
    return [f"src/{rel}", rel]


def show_revision(hub: Path, rev: str, path: str) -> str | None:
    """Return `git show <rev>:<path>` output, or None when absent."""
    proc = subprocess.run(
        ["git", "show", f"{rev}:{path}"],
        cwd=hub,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def module_from_source(name: str, source: str) -> ModuleType:
    """Execute source as a fresh temporary module."""
    module = ModuleType(name)
    exec(compile(source, f"<{name}>", "exec"), module.__dict__)
    return module


def active_ids_at_revision(hub: Path, module_name: str, rev: str) -> set[str]:
    """Active block ids of the registry module at a git revision (SPEC §15.3)."""
    for path in module_paths(module_name):
        source = show_revision(hub, rev, path)
        if source is not None:
            return {b.id for b in get_registry(module_from_source("helios_rev", source)).active()}
    raise ValueError(f"program module {module_name!r} not found at revision {rev!r}")


def diff_ids(old: set[str], new: set[str]) -> list[str]:
    """`+<id>` lines (in new, not old) sorted, then `-<id>` lines sorted (SPEC §15.3)."""
    lines = [f"+{i}" for i in sorted(new - old)]
    lines.extend(f"-{i}" for i in sorted(old - new))
    return lines
