"""Project configuration (SPEC §5).

The loader walks up from ``start`` to the first directory holding
``.agents/workflow.toml``, else the first holding ``.git``, else ``start``;
that directory is the hub. Without the file helios runs in ad hoc mode with
the defaults from the SPEC table. An unknown key at any depth, and a value
of the wrong type, are errors naming the dotted key.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WORKFLOW_REL = Path(".agents/workflow.toml")


class ConfigError(ValueError):
    """A SPEC §5 configuration refusal.

    ``load`` raises this for every unreadable or invalid ``workflow.toml``, so the command layer
    prints it with a ``helios: `` prefix and exits 2 instead of showing a traceback (SPEC §2.3).
    It subclasses ``ValueError`` because the handlers written before it catch that.
    """

_VERIFY_KINDS = ("verify-code", "verify-math", "verify-validate")

_STR = "str"
_OPT_STR = "opt_str"
_STR_LIST = "str_list"
_INT = "int"
_BOOL = "bool"

_PROJECT_TYPES: dict[str, str] = {
    "test": _STR,
    "typecheck": _STR,
    "units": _STR,
    "verify_artifacts": _STR,
    "experiments": _STR,
    "worktrees": _STR,
    "runs": _STR,
    "link_into_worktrees": _STR_LIST,
    "confidential": _STR_LIST,
    "always_allowed": _STR_LIST,
    "program": _OPT_STR,
    "claims": _OPT_STR,
}

_AGENTS_TYPES: dict[str, str] = {
    "orchestrate": _STR,
    "implement": _STR,
    "model": _STR,
    "verify_code": _STR,
    "verify_math": _STR,
    "verify_validate": _STR,
    "verify_order": _STR_LIST,
}

_HARNESS_TYPES: dict[str, str] = {
    "binary": _STR,
    "model": _OPT_STR,
    "effort": _OPT_STR,
    "timeout_s": _INT,
    "extra_args": _STR_LIST,
    "server_url": _OPT_STR,
}

_CONTROL_TYPES: dict[str, str] = {
    "default": _STR,
    "until": _STR,
    "stop_at": _STR_LIST,
    "confirm": _STR_LIST,
}

_MEMORY_TYPES: dict[str, str] = {
    "backend": _STR,
    "export_dir": _STR,
    "inject_cap_bytes": _INT,
}

_TELEMETRY_TYPES: dict[str, str] = {
    "enabled": _BOOL,
    "endpoint": _STR,
    "timeout_s": _INT,
    "service_name": _STR,
}

_SECTIONS = {"project", "agents", "harness", "control", "memory", "telemetry", "tolerance"}

_CHECK_TYPES: dict[str, str] = {
    "name": _STR,
    "command": _STR,
    "when": _STR,
}

_CHECK_NAME_RE = re.compile(r"[a-z0-9_-]{1,32}")
_CHECK_RESERVED_NAMES = ("report", "ownership")
_CHECK_WHEN_VALUES = ("run", "merge", "both")


@dataclass(frozen=True)
class CheckEntry:
    """One ordered, named `[[project.check]]` entry (SPEC section 5)."""

    name: str
    command: str
    when: str = "both"


@dataclass(frozen=True)
class ProjectConfig:
    test: str = "uv run pytest -q"
    typecheck: str = ""
    check: tuple[CheckEntry, ...] = ()
    units: str = "docs/units"
    verify_artifacts: str = "tools/verify"
    experiments: str = "experiments"
    worktrees: str = ".claude/worktrees"
    link_into_worktrees: tuple[str, ...] = ()
    runs: str = ".helios/runs"
    confidential: tuple[str, ...] = ()
    always_allowed: tuple[str, ...] = ("tests/",)
    program: str | None = None
    claims: str | None = None


@dataclass(frozen=True)
class AgentsConfig:
    orchestrate: str = "claude"
    implement: str = "codex"
    model: str = "claude"
    verify_code: str = "other"
    verify_math: str = "other"
    verify_validate: str = "other"
    verify_order: tuple[str, ...] = ("claude", "codex", "opencode", "agy")


@dataclass(frozen=True)
class HarnessConfig:
    binary: str = ""
    model: str | None = None
    effort: str | None = None
    timeout_s: int = 3600
    extra_args: tuple[str, ...] = ()
    server_url: str | None = None

    def resolved_binary(self, name: str) -> str:
        return self.binary or name


@dataclass(frozen=True)
class ControlConfig:
    default: str = "manual"
    until: str = "verify-code"
    stop_at: tuple[str, ...] = ("frame", "model", "report")
    confirm: tuple[str, ...] = ("merge",)


@dataclass(frozen=True)
class MemoryConfig:
    backend: str = "beads"
    export_dir: str = ".helios/memories"
    inject_cap_bytes: int = 32000


@dataclass(frozen=True)
class TelemetryConfig:
    """The ``[telemetry]`` block (SPEC section 21): export OpenTelemetry spans over OTLP."""

    enabled: bool = False
    endpoint: str = ""
    timeout_s: int = 5
    service_name: str = "helios"


@dataclass(frozen=True)
class Config:
    hub: Path
    project: ProjectConfig = field(default_factory=ProjectConfig)
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    harness: dict[str, HarnessConfig] = field(default_factory=dict)
    control: ControlConfig = field(default_factory=ControlConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    tolerance: dict[str, float] = field(default_factory=dict)


def find_hub(start: Path) -> Path:
    """Walk up to the workflow file, else the first ``.git``, else ``start`` (SPEC §5)."""
    start = start.resolve()
    with_git: Path | None = None
    current: Path | None = start
    while current is not None:
        if (current / WORKFLOW_REL).is_file():
            return current
        if with_git is None and (current / ".git").exists():
            with_git = current
        parent = current.parent
        current = parent if parent != current else None
    return with_git if with_git is not None else start


def _check_type(dotted: str, value: Any, want: str) -> None:
    """Raise TypeError naming the dotted key when the value has the wrong type."""
    if want == _STR:
        ok = isinstance(value, str)
    elif want == _OPT_STR:
        ok = value is None or isinstance(value, str)
    elif want == _INT:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif want == _BOOL:
        ok = isinstance(value, bool)
    elif want == _STR_LIST:
        ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
    else:
        raise AssertionError(f"unknown type tag {want!r}")
    if not ok:
        raise TypeError(f"config key {dotted!r} has the wrong type: {value!r}")


def _validated_table(table: Any, types: dict[str, str], prefix: str) -> dict:
    """Type-check a section table, then return it as a dict (SPEC §5)."""
    _check_table(table, types, prefix)
    assert isinstance(table, dict)
    return dict(table)


def _check_table(table: Any, types: dict[str, str], prefix: str) -> None:
    if not isinstance(table, dict):
        raise TypeError(f"config key {prefix!r} must be a table")
    for key, value in table.items():
        dotted = f"{prefix}.{key}"
        if key not in types:
            raise ValueError(f"unknown config key {dotted!r}")
        _check_type(dotted, value, types[key])


def _check_entries(raw: Any) -> tuple[CheckEntry, ...]:
    """Validate `[[project.check]]` and return its entries (SPEC section 5)."""
    if not isinstance(raw, list):
        raise TypeError(f"config key 'project.check' has the wrong type: {raw!r}")
    entries: list[CheckEntry] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        prefix = f"project.check[{i}]"
        if not isinstance(item, dict):
            raise TypeError(f"config key {prefix!r} has the wrong type: {item!r}")
        for key, value in item.items():
            if key not in _CHECK_TYPES:
                raise ValueError(f"unknown config key '{prefix}.{key}'")
            _check_type(f"{prefix}.{key}", value, _CHECK_TYPES[key])
        if "name" not in item:
            raise ValueError(f"config key '{prefix}.name' is required")
        if "command" not in item:
            raise ValueError(f"config key '{prefix}.command' is required")
        name = item["name"]
        command = item["command"]
        when = item.get("when", "both")
        if not _CHECK_NAME_RE.fullmatch(name):
            raise ValueError(f"project.check[{i}].name must match [a-z0-9_-]{{1,32}}")
        if name in seen:
            raise ValueError(f"project.check[{i}].name {name} is a duplicate")
        seen.add(name)
        if name in _CHECK_RESERVED_NAMES:
            raise ValueError(f"project.check[{i}].name {name} is reserved")
        if when not in _CHECK_WHEN_VALUES:
            raise ValueError(f'project.check[{i}].when must be "run", "merge" or "both"')
        if name == "test" and when != "merge":
            raise ValueError(f'project.check[{i}] name "test" requires when = "merge"')
        entries.append(CheckEntry(name=name, command=command, when=when))
    return tuple(entries)


def _project(table: dict) -> ProjectConfig:
    data = dict(table)
    check_raw = data.pop("check", None)
    _check_table(data, _PROJECT_TYPES, "project")
    entries: tuple[CheckEntry, ...] = ()
    if check_raw is not None:
        entries = _check_entries(check_raw)
    if entries and ("test" in data or "typecheck" in data):
        raise ValueError("project.test and project.typecheck cannot be set with project.check")
    for key in ("link_into_worktrees", "confidential", "always_allowed"):
        if key in data:
            data[key] = tuple(data[key])
    return ProjectConfig(check=entries, **data)


def effective_checks(project: ProjectConfig) -> tuple[CheckEntry, ...]:
    """Configured checks, applying the `project.test`/`project.typecheck` sugar (SPEC section 5).

    With no `[[project.check]]` entry, `project.test` and `project.typecheck` are read
    as exactly two entries in this order: `name = "test"` with `when = "merge"`, then
    `name = "typecheck"` with `when = "both"`. An empty command still produces the
    entry; the caller skips a disabled (empty command) entry.
    """
    if project.check:
        return project.check
    return (
        CheckEntry(name="test", command=project.test, when="merge"),
        CheckEntry(name="typecheck", command=project.typecheck, when="both"),
    )


def _agents(table: dict) -> AgentsConfig:
    _check_table(table, _AGENTS_TYPES, "agents")
    data = dict(table)
    if "verify_order" in data:
        data["verify_order"] = tuple(data["verify_order"])
    return AgentsConfig(**data)


def _harness(table: dict) -> dict[str, HarnessConfig]:
    if not isinstance(table, dict):
        raise TypeError("config key 'harness' must be a table")
    out: dict[str, HarnessConfig] = {}
    for name, sub in table.items():
        if not isinstance(sub, dict):
            raise TypeError(f"config key 'harness.{name}' must be a table")
        _check_table(sub, _HARNESS_TYPES, f"harness.{name}")
        if name != "opencode" and "server_url" in sub:
            raise ValueError(
                f"unknown config key 'harness.{name}.server_url' (only harness.opencode has it)"
            )
        data = dict(sub)
        if "extra_args" in data:
            data["extra_args"] = tuple(data["extra_args"])
        out[name] = HarnessConfig(**data)
    return out


def _tolerance(table: Any) -> dict[str, float]:
    if not isinstance(table, dict):
        raise TypeError("config key 'tolerance' must be a table")
    out: dict[str, float] = {}
    for tier, value in table.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"config key 'tolerance.{tier}' must be a number: {value!r}")
        out[tier] = float(value)
    return out


def load(start: Path) -> Config:
    """Load the project configuration, defaulting when the file is absent (SPEC §5).

    Every failure below leaves as a ``ConfigError`` naming the dotted key, so no caller has to
    know which of ``ValueError`` or ``TypeError`` a particular check happens to raise.
    """
    hub = find_hub(start)
    path = hub / WORKFLOW_REL
    if not path.is_file():
        return Config(hub=hub)
    try:
        return _load_file(hub, path)
    except ConfigError:
        raise
    except (TypeError, ValueError) as exc:
        raise ConfigError(str(exc)) from exc


def _load_file(hub: Path, path: Path) -> Config:
    """Read and validate an existing workflow.toml."""
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    for key in raw:
        if key not in _SECTIONS:
            raise ValueError(f"unknown config key {key!r}")
    project = _project(raw.get("project", {}))
    agents = _agents(raw.get("agents", {}))
    control_raw = _validated_table(raw.get("control", {}), _CONTROL_TYPES, "control")
    for key in ("stop_at", "confirm"):
        if key in control_raw:
            control_raw[key] = tuple(control_raw[key])
    memory_raw = _validated_table(raw.get("memory", {}), _MEMORY_TYPES, "memory")
    telemetry_raw = _validated_table(raw.get("telemetry", {}), _TELEMETRY_TYPES, "telemetry")
    telemetry = TelemetryConfig(**telemetry_raw)
    if telemetry.enabled and not telemetry.endpoint:
        raise ValueError("telemetry.enabled requires telemetry.endpoint")
    return Config(
        hub=hub,
        project=project,
        agents=agents,
        harness=_harness(raw.get("harness", {})),
        control=ControlConfig(**control_raw),
        memory=MemoryConfig(**memory_raw),
        telemetry=telemetry,
        tolerance=_tolerance(raw.get("tolerance", {})),
    )


def split_spec(spec: str) -> tuple[str, str | None]:
    """Split an agent spec into (harness, profile); ``other`` has no profile."""
    if spec == "other":
        return ("other", None)
    harness, sep, profile = spec.partition(":")
    return (harness, profile if sep else None)


def author_harness(author: str | None) -> str | None:
    """The harness part of an author agent spec, before any ``:`` (SPEC §5)."""
    if author is None:
        return None
    return author.partition(":")[0]


def resolve_harness(spec: str, *, author: str | None, verify_order: tuple[str, ...]) -> str:
    """Resolve an agent spec to a harness name (SPEC §5).

    ``other`` is the first harness in ``verify_order`` that differs from the
    author's harness. A plain spec resolves to itself.
    """
    harness, _profile = split_spec(spec)
    if harness != "other":
        return harness
    excluded = author_harness(author)
    for candidate in verify_order:
        if candidate != excluded:
            return candidate
    raise ValueError("verify_order has no harness differing from the author")


def spec_for_kind(config: Config, kind: str) -> str:
    """Return the configured agent spec for a bead kind (SPEC §5)."""
    agents = config.agents
    if kind in ("impl", "validate"):
        return agents.implement
    if kind == "model":
        return agents.model
    if kind == "verify-code":
        return agents.verify_code
    if kind == "verify-math":
        return agents.verify_math
    if kind == "verify-validate":
        return agents.verify_validate
    raise ValueError(f"unknown bead kind {kind!r}")


def harness_for_kind(
    config: Config, kind: str, *, author: str | None = None, override: str | None = None
) -> str:
    """Resolve the harness for a bead kind; ``override`` wins (SPEC §7.1 step 3)."""
    spec = override if override is not None else spec_for_kind(config, kind)
    return resolve_harness(spec, author=author, verify_order=config.agents.verify_order)


def is_verify_kind(kind: str) -> bool:
    return kind in _VERIFY_KINDS
