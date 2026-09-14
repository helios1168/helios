"""Program registry and program command library (SPEC §15.1, §15.3)."""

from __future__ import annotations

import importlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

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

DIFF_TIMEOUT_S = 120


@dataclass
class Block:
    """One program block (SPEC §15.1)."""

    id: str
    kind: str
    expr: Any
    build: Callable[..., Any] | None = None
    satisfies: Iterable[str] = ()
    relaxes: Iterable[str] = ()
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


def forget_module(name: str) -> None:
    """Drop a module and its package parents from sys.modules (SPEC §2.3)."""
    parts = name.split(".")
    for i in range(1, len(parts) + 1):
        sys.modules.pop(".".join(parts[:i]), None)


def load_module(name: str, hub: Path) -> ModuleType:
    """Import a dotted module from the hub, freshly (SPEC §15.2 step 3).

    Any import failure becomes ImportError shaped for SPEC §2.3:
    `cannot import <module>: <exception type>: <message>`.
    """
    ensure_hub_path(hub)
    forget_module(name)
    importlib.invalidate_caches()
    try:
        return importlib.import_module(name)
    except Exception as exc:
        raise ImportError(f"cannot import {name}: {type(exc).__name__}: {exc}") from exc


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


def one_space(text: str) -> str:
    """Replace each CR LF, CR and LF with one space (SPEC §15.3)."""
    return text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def format_block(block: Block) -> str:
    """One `program show` line (SPEC §15.3)."""
    expr = one_space(str(block.expr))
    halves = []
    if block.satisfies:
        halves.append("satisfies " + ", ".join(sorted(block.satisfies)))
    if block.relaxes:
        halves.append("relaxes " + ", ".join(sorted(block.relaxes)))
    line = f"{block.id}  {expr}"
    if halves:
        line += "  # " + " / ".join(halves)
    return line


def show_text(registry: Registry) -> str:
    """Deterministic markdown for the registry; empty gives zero bytes (SPEC §15.3)."""
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
    if not lines:
        return ""
    return "".join(line + "\n" for line in lines)


class _RecorderBase:
    """Operand support shared by the recording model and every recorder (SPEC §15.3)."""

    __slots__ = ()

    _sink: list[tuple[str, str]]
    _kind: list[str]

    def _fresh(self) -> _Recorder:
        return _Recorder(self._sink, self._kind)

    def __getattr__(self, item: str) -> _Recorder:
        return self._fresh()

    def __call__(self, *args: Any, **kwargs: Any) -> _Recorder:
        return self._fresh()

    def __getitem__(self, key: Any) -> _Recorder:
        return self._fresh()

    def __iter__(self) -> Any:
        return iter(())

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return True

    def __hash__(self) -> int:
        return object.__hash__(self)

    def __add__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __radd__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __sub__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __rsub__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __mul__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __rmul__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __matmul__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __rmatmul__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __truediv__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __rtruediv__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __floordiv__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __rfloordiv__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __mod__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __rmod__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __divmod__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __rdivmod__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __pow__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __rpow__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __lshift__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __rlshift__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __rshift__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __rrshift__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __and__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __rand__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __xor__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __rxor__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __or__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __ror__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __lt__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __le__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __eq__(self, other: Any) -> Any:
        return self._fresh()

    def __ne__(self, other: Any) -> Any:
        return self._fresh()

    def __gt__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __ge__(self, other: Any) -> _Recorder:
        return self._fresh()

    def __neg__(self) -> _Recorder:
        return self._fresh()

    def __pos__(self) -> _Recorder:
        return self._fresh()

    def __abs__(self) -> _Recorder:
        return self._fresh()

    def __invert__(self) -> _Recorder:
        return self._fresh()

    def __round__(self, ndigits: Any = None) -> _Recorder:
        return self._fresh()


class _Recorder(_RecorderBase):
    """Records kwargs["name"] when it is a string; every step returns a recorder."""

    __slots__ = ("_sink", "_kind")

    def __init__(self, sink: list[tuple[str, str]], kind: list[str]) -> None:
        object.__setattr__(self, "_sink", sink)
        object.__setattr__(self, "_kind", kind)

    def __call__(self, *args: Any, **kwargs: Any) -> _Recorder:
        name = kwargs.get("name")
        if isinstance(name, str):
            self._sink.append((self._kind[0], name))
        return _Recorder(self._sink, self._kind)


class _RecordingModel(_RecorderBase):
    """Returns a recorder for any attribute (SPEC §15.3)."""

    __slots__ = ("_sink", "_kind")

    def __init__(self, sink: list[tuple[str, str]], kind: list[str]) -> None:
        object.__setattr__(self, "_sink", sink)
        object.__setattr__(self, "_kind", kind)


def check_registry(registry: Registry, data: Any) -> tuple[list[str], list[str]]:
    """Build active blocks and compare recorded names (SPEC §15.3).

    Returns (missing, unexpected); builds that raise are ignored here, see
    check_registry_full for the error lines.
    """
    missing, unexpected, _ = check_registry_full(registry, data)
    return missing, unexpected


def check_registry_full(
    registry: Registry, data: Any
) -> tuple[list[str], list[str], list[str]]:
    """Build active blocks on a recording model (SPEC §15.3).

    Returns (missing, unexpected, errors), all sorted except errors which follow
    block order. A build that raises gives `error: <id>: <type>: <message>`.
    """
    sink: list[tuple[str, str]] = []
    kind: list[str] = [""]
    model = _RecordingModel(sink, kind)
    errors: list[str] = []
    for block in registry.active():
        if block.build is None:
            continue
        kind[0] = block.kind
        try:
            block.build(model, data)
        except Exception as exc:
            errors.append(f"error: {block.id}: {type(exc).__name__}: {exc}")
    recorded = {name.split("[", 1)[0] for k, name in sink if k in CHECKED_KINDS}
    expected = {
        b.id for b in registry.active() if b.kind in CHECKED_KINDS and b.build is not None
    }
    return sorted(expected - recorded), sorted(recorded - expected), errors


def module_relpath(module_name: str) -> str:
    """Dotted module name as a relative path."""
    return module_name.replace(".", "/") + ".py"


_CHILD_SCRIPT = (
    "import importlib, json, sys\n"
    "name, root = sys.argv[1], sys.argv[2]\n"
    "sys.path.insert(0, root)\n"
    "sys.path.insert(0, root + '/src')\n"
    "try:\n"
    "    module = importlib.import_module(name)\n"
    "    ids = sorted(b.id for b in module.REGISTRY.active())\n"
    "except BaseException as exc:\n"
    "    print(json.dumps({'error': f'{type(exc).__name__}: {exc}'}))\n"
    "    raise SystemExit(1)\n"
    "print(json.dumps(ids))\n"
)


def extract_revision(hub: Path, rev: str, target: Path) -> None:
    """Extract `git archive <rev>` into target; ValueError when unreadable."""
    proc = subprocess.run(["git", "archive", rev], cwd=hub, capture_output=True)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        tail = detail[-1] if detail else f"exit {proc.returncode}"
        raise ValueError(f"cannot read revision {rev!r}: {tail}")
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        tar.extractall(target, filter="fully_trusted")


def import_ids_at_revision(module_name: str, root: Path, rev: str) -> set[str]:
    """Import the module with root and root/src first on sys.path (SPEC §15.3)."""
    env = dict(os.environ)
    env.pop(OMIT_ENV, None)
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD_SCRIPT, module_name, str(root)],
            capture_output=True,
            text=True,
            env=env,
            timeout=DIFF_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise ImportError(
            f"cannot import {module_name} at {rev}: TimeoutExpired: timed out"
        ) from None
    if proc.returncode == 0:
        try:
            ids = json.loads(proc.stdout)
        except json.JSONDecodeError:
            ids = None
        if isinstance(ids, list) and all(isinstance(i, str) for i in ids):
            return set(ids)
        raise ImportError(
            f"cannot import {module_name} at {rev}: ImportError: unexpected output"
        )
    text = proc.stdout.strip().splitlines()
    err = ""
    if text:
        try:
            payload = json.loads(text[-1])
            if isinstance(payload, dict):
                err = str(payload.get("error", ""))
        except json.JSONDecodeError:
            err = ""
    if not err:
        err = f"exit {proc.returncode}"
    raise ImportError(f"cannot import {module_name} at {rev}: {err}")


def active_ids_at_revision(hub: Path, module_name: str, rev: str) -> set[str]:
    """Active block ids of the registry module at a git revision (SPEC §15.3)."""
    rel = module_relpath(module_name)
    with tempfile.TemporaryDirectory(prefix="helios-diff-") as tmp:
        root = Path(tmp)
        extract_revision(hub, rev, root)
        if not (root / "src" / rel).is_file() and not (root / rel).is_file():
            raise ValueError(f"program module {module_name!r} not found at revision {rev!r}")
        return import_ids_at_revision(module_name, root, rev)


def diff_ids(old: set[str], new: set[str]) -> list[str]:
    """`+<id>` lines (in new, not old) sorted, then `-<id>` lines sorted (SPEC §15.3)."""
    lines = [f"+{i}" for i in sorted(new - old)]
    lines.extend(f"-{i}" for i in sorted(old - new))
    return lines
