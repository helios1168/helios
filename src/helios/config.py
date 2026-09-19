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

from helios import stageset

WORKFLOW_REL = Path(".agents/workflow.toml")


class ConfigError(ValueError):
    """A SPEC §5 configuration refusal.

    ``load`` raises this for every unreadable or invalid ``workflow.toml``, so the command layer
    prints it with a ``helios: `` prefix and exits 2 instead of showing a traceback (SPEC §2.3).
    It subclasses ``ValueError`` because the handlers written before it catch that.
    """

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

_SECTIONS = {"project", "agents", "harness", "control", "memory", "telemetry", "tolerance", "stage"}

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


#: The roles the shipped research stage set names, with their defaults (SPEC §5).
DEFAULT_ROLES: dict[str, str] = {
    "orchestrate": "claude",
    "implement": "codex",
    "model": "claude",
    "verify_code": "other",
    "verify_math": "other",
    "verify_validate": "other",
}


@dataclass(frozen=True, init=False)
class AgentsConfig:
    """``[agents]`` (SPEC §5). Open keyed: any key is a role a stage's ``author`` may name.

    The six documented roles keep their defaults and their attribute names, because the shipped
    research stage set names them, but a hub may declare any role its own stages ask for. A role
    may be passed by name, so ``AgentsConfig(verify_code="claude")`` still reads as it did when
    the six were fields.
    """

    roles: dict[str, str]
    verify_order: tuple[str, ...]

    def __init__(
        self,
        roles: dict[str, str] | None = None,
        verify_order: tuple[str, ...] | list[str] = ("claude", "codex", "opencode", "agy"),
        **named_roles: str,
    ) -> None:
        merged = dict(DEFAULT_ROLES)
        merged.update(roles or {})
        merged.update(named_roles)
        object.__setattr__(self, "roles", merged)
        object.__setattr__(self, "verify_order", tuple(verify_order))

    def spec(self, role: str) -> str:
        """The agent spec configured for a role, or raise ``KeyError`` naming the declared roles."""
        try:
            return self.roles[role]
        except KeyError:
            raise KeyError(
                f"{role!r} is not a key of [agents]; declared: {', '.join(sorted(self.roles))}"
            ) from None

    @property
    def orchestrate(self) -> str:
        return self.roles.get("orchestrate", DEFAULT_ROLES["orchestrate"])

    @property
    def implement(self) -> str:
        return self.roles.get("implement", DEFAULT_ROLES["implement"])

    @property
    def model(self) -> str:
        return self.roles.get("model", DEFAULT_ROLES["model"])

    @property
    def verify_code(self) -> str:
        return self.roles.get("verify_code", DEFAULT_ROLES["verify_code"])

    @property
    def verify_math(self) -> str:
        return self.roles.get("verify_math", DEFAULT_ROLES["verify_math"])

    @property
    def verify_validate(self) -> str:
        return self.roles.get("verify_validate", DEFAULT_ROLES["verify_validate"])


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
    stages: stageset.StageSet = field(default_factory=stageset.research)


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
    """Read ``[agents]`` as roles plus ``verify_order`` (SPEC §5).

    Any key other than ``verify_order`` is a role name whose value is an agent spec. A typo is
    caught where it matters, when a stage's ``author`` names a role that is not here (§3.1).
    """
    if not isinstance(table, dict):
        raise TypeError("config key 'agents' must be a table")
    roles = dict(DEFAULT_ROLES)
    verify_order = AgentsConfig().verify_order
    for key, value in table.items():
        if key == "verify_order":
            _check_type("agents.verify_order", value, _STR_LIST)
            verify_order = tuple(value)
            continue
        _check_type(f"agents.{key}", value, _STR)
        roles[key] = value
    return AgentsConfig(roles=roles, verify_order=verify_order)


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
    declared = "stage" in raw
    stages = stageset.parse(raw["stage"]) if declared else stageset.research()
    _check_stage_authors(stages, agents)
    control_raw = _validated_table(raw.get("control", {}), _CONTROL_TYPES, "control")
    for key in ("stop_at", "confirm"):
        if key in control_raw:
            control_raw[key] = tuple(control_raw[key])
    if declared:
        # The research defaults name research stages, so a hub with its own stage set starts from
        # nothing rather than from ids it never declared (SPEC §5).
        control_raw.setdefault("until", "")
        control_raw.setdefault("stop_at", ())
    for stage_id in control_raw.get("stop_at", ()):
        if stage_id not in stages:
            raise ValueError(
                f"control.stop_at names {stage_id!r}, which is not a declared stage; "
                f"declared: {', '.join(stages.ids)}"
            )
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
        stages=stages,
    )


def _check_stage_authors(stages: stageset.StageSet, agents: AgentsConfig) -> None:
    """Every stage's ``author`` must name a role ``[agents]`` declares (SPEC §3.1).

    This refusal is what replaces the closed key list on ``[agents]`` as the defence against a
    typo: a misspelled role is caught here instead of at the moment a run needs it.
    """
    for stage in stages:
        if stage.author == stageset.OTHER:
            continue
        if stage.author not in agents.roles:
            raise ValueError(
                f"stage {stage.id} author {stage.author!r} is not a key of [agents]; "
                f"declared: {', '.join(sorted(agents.roles))}"
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
    """The configured agent spec for a bead kind, through its stage (SPEC §3.1, §5)."""
    stage = config.stages.find(kind)
    if stage is None:
        raise ValueError(f"unknown bead kind {kind!r}")
    if stage.author == stageset.OTHER:
        return stageset.OTHER
    try:
        return config.agents.spec(stage.author)
    except KeyError as exc:
        raise ValueError(f"stage {kind} author {stage.author!r} is not a key of [agents]") from exc


def harness_for_kind(
    config: Config, kind: str, *, author: str | None = None, override: str | None = None
) -> str:
    """Resolve the harness for a bead kind; ``override`` wins (SPEC §7.1 step 3)."""
    spec = override if override is not None else spec_for_kind(config, kind)
    return resolve_harness(spec, author=author, verify_order=config.agents.verify_order)
