"""Project configuration (SPEC §5).

The loader walks up from ``start`` to the first directory holding
``.agents/workflow.toml``, else the first holding ``.git``, else ``start``;
that directory is the hub. Without the file helios runs in ad hoc mode with
the defaults from the SPEC table. Unknown keys are an error naming the key.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

WORKFLOW_REL = Path(".agents/workflow.toml")

_VERIFY_KINDS = ("verify-code", "verify-math", "verify-validate")

_HARNESS_SUBKEYS = {"binary", "model", "effort", "timeout_s", "extra_args", "server_url"}


@dataclass(frozen=True)
class ProjectConfig:
    test: str = "uv run pytest -q"
    typecheck: str = ""
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
class Config:
    hub: Path
    project: ProjectConfig = field(default_factory=ProjectConfig)
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    harness: dict[str, HarnessConfig] = field(default_factory=dict)
    control: ControlConfig = field(default_factory=ControlConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
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


def _unknown(key: str) -> ValueError:
    return ValueError(f"unknown config key {key!r}")


def _check_keys(table: dict, known: set[str], prefix: str) -> None:
    for key in table:
        if key not in known:
            raise _unknown(f"{prefix}.{key}" if prefix else key)


_PROJECT_KEYS = {f.name for f in ProjectConfig.__dataclass_fields__.values()} | {"test"}
_AGENTS_KEYS = set(AgentsConfig.__dataclass_fields__)
_CONTROL_KEYS = set(ControlConfig.__dataclass_fields__)
_MEMORY_KEYS = set(MemoryConfig.__dataclass_fields__)


def _project(table: dict) -> ProjectConfig:
    _check_keys(table, _PROJECT_KEYS, "project")
    data = dict(table)
    for key in ("link_into_worktrees", "confidential", "always_allowed"):
        if key in data:
            data[key] = tuple(data[key])
    return ProjectConfig(**data)


def _agents(table: dict) -> AgentsConfig:
    _check_keys(table, _AGENTS_KEYS, "agents")
    data = dict(table)
    if "verify_order" in data:
        data["verify_order"] = tuple(data["verify_order"])
    return AgentsConfig(**data)


def _harness(table: dict) -> dict[str, HarnessConfig]:
    out: dict[str, HarnessConfig] = {}
    for name, sub in table.items():
        if not isinstance(sub, dict):
            raise _unknown(f"harness.{name}")
        for key in sub:
            if key not in _HARNESS_SUBKEYS:
                raise _unknown(f"harness.{name}.{key}")
        data = dict(sub)
        if "extra_args" in data:
            data["extra_args"] = tuple(data["extra_args"])
        out[name] = HarnessConfig(**data)
    return out


def load(start: Path) -> Config:
    """Load the project configuration, defaulting when the file is absent (SPEC §5)."""
    hub = find_hub(start)
    path = hub / WORKFLOW_REL
    if not path.is_file():
        return Config(hub=hub)
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    for key in raw:
        if key not in {"project", "agents", "harness", "control", "memory", "tolerance"}:
            raise _unknown(key)
    tolerance: dict[str, float] = {}
    if "tolerance" in raw:
        if not isinstance(raw["tolerance"], dict):
            raise ValueError("tolerance must be a table")
        tolerance = {k: float(v) for k, v in raw["tolerance"].items()}
    project = _project(raw.get("project", {}))
    agents = _agents(raw.get("agents", {}))
    control_raw = _checked(raw, "control")
    for key in ("stop_at", "confirm"):
        if key in control_raw:
            control_raw[key] = tuple(control_raw[key])
    memory = MemoryConfig(**_checked(raw, "memory"))
    return Config(
        hub=hub,
        project=project,
        agents=agents,
        harness=_harness(raw.get("harness", {})),
        control=ControlConfig(**control_raw),
        memory=memory,
        tolerance=tolerance,
    )


def _checked(raw: dict, section: str) -> dict:
    table = raw.get(section, {})
    known = _CONTROL_KEYS if section == "control" else _MEMORY_KEYS
    _check_keys(table, known, section)
    return table


def split_spec(spec: str) -> tuple[str, str | None]:
    """Split an agent spec into (harness, profile); ``other`` has no profile."""
    if spec == "other":
        return ("other", None)
    harness, sep, profile = spec.partition(":")
    return (harness, profile if sep else None)


def resolve_harness(spec: str, *, author: str | None, verify_order: tuple[str, ...]) -> str:
    """Resolve an agent spec to a harness name (SPEC §5).

    ``other`` is the first harness in ``verify_order`` that differs from the
    author of the bead the verifier checks. A plain spec resolves to itself.
    """
    harness, _profile = split_spec(spec)
    if harness != "other":
        return harness
    for candidate in verify_order:
        if candidate != author:
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
        return agents.verify_code
    raise ValueError(f"unknown bead kind {kind!r}")


def harness_for_kind(
    config: Config, kind: str, *, author: str | None = None, override: str | None = None
) -> str:
    """Resolve the harness for a bead kind; ``override`` wins (SPEC §7.1 step 3)."""
    spec = override if override is not None else spec_for_kind(config, kind)
    return resolve_harness(spec, author=author, verify_order=config.agents.verify_order)


def is_verify_kind(kind: str) -> bool:
    return kind in _VERIFY_KINDS
