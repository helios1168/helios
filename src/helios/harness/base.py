"""Harness adapter interface (SPEC §6).

An adapter turns a LaunchSpec into argv for one agent CLI and turns what the process left
behind into a NativeResult. It never runs the process: helios.run owns subprocess, timeout,
signals, report validation and write-back, so every harness is launched and judged the same way.

Implementations: harness/claude.py, codex.py, opencode.py, agy.py, fake.py (tests).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class LaunchSpec:
    """Everything an adapter needs to build one turn of one attempt."""

    bead: str
    attempt: int
    worktree: Path  # cwd of the process
    prompt: str  # identical bytes for every harness (SPEC §7.2)
    report_path: Path  # where the agent must write its AgentReport JSON
    report_schema_path: Path  # schemas/agent-report.schema.json
    raw_dir: Path  # adapter may place native side files here (last message, events)
    model: str | None = None
    effort: str | None = None
    timeout_s: int = 3600
    resume_session: str | None = None  # continue this native session instead of starting fresh
    server_url: str | None = None  # opencode only: attach to a running `opencode serve`
    extra_args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class NativeResult:
    """What the adapter could read from the finished process.

    `structured` is the report the harness returned through its own schema channel (claude
    structured_output, codex last message, agy json result), or None when the harness has no
    such channel or it was empty. helios.run falls back to the report file when it is None.
    """

    session_id: str | None
    structured: dict[str, Any] | None
    native_error: str | None = None  # the harness said the turn failed (is_error, error event)
    notes: tuple[str, ...] = ()


@runtime_checkable
class Harness(Protocol):
    name: str

    def argv(self, spec: LaunchSpec) -> list[str]:
        """Full argv, binary first, for a fresh turn or, with spec.resume_session, a resumed one.

        The prompt is passed as the last argument or on stdin; `stdin_text` says which.
        """
        ...

    def stdin_text(self, spec: LaunchSpec) -> str | None:
        """Text to write to stdin, or None when the prompt is in argv."""
        ...

    def parse(self, spec: LaunchSpec, exit_code: int | None, stdout_path: Path) -> NativeResult:
        """Read captured stdout (and raw_dir side files) after the process ends.

        Must not raise on malformed output: return session_id None, structured None and a
        native_error describing what was wrong. helios.run turns that into an execution status.
        """
        ...

    def attach_command(self, session_id: str, spec: LaunchSpec) -> list[str]:
        """argv a human runs to open this session interactively (helios attach)."""
        ...
