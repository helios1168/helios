"""Codex CLI harness adapter (SPEC §6.3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from helios.harness.base import Harness, LaunchSpec, NativeResult

LAST_MESSAGE = "last-message.json"


class CodexAdapter(Harness):
    """Adapter for the Codex CLI (SPEC §6.3)."""

    name = "codex"

    def argv(self, spec: LaunchSpec) -> list[str]:
        """Build argv for a fresh or resumed turn (SPEC §6.3)."""
        out = ["codex", "exec"]
        if spec.resume_session:
            out += ["resume", spec.resume_session]
        out += [
            "--json",
            "--output-schema",
            str(spec.report_schema_path),
            "-o",
            str(spec.raw_dir / LAST_MESSAGE),
        ]
        if not spec.resume_session:
            out += ["-C", str(spec.worktree)]
        out += ["--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox"]
        if spec.model:
            out += ["-m", spec.model]
        if spec.effort:
            out += ["-c", f'model_reasoning_effort="{spec.effort}"']
        out += list(spec.extra_args)
        out.append("-")
        return out

    def stdin_text(self, spec: LaunchSpec) -> str | None:
        """The prompt goes on stdin for codex (SPEC §6.3)."""
        return spec.prompt

    def parse(
        self, spec: LaunchSpec, exit_code: int | None, stdout_path: Path
    ) -> NativeResult:
        """Parse the JSON lines stdout (SPEC §6.4)."""
        try:
            text = stdout_path.read_text()
        except OSError:
            return NativeResult(session_id=None, structured=None, native_error="no stdout")
        events, skipped = _parse_lines(text)
        notes: list[str] = []
        if skipped:
            notes.append(f"skipped {skipped} non-JSON lines")
        if not events:
            return NativeResult(
                session_id=None,
                structured=None,
                native_error="no events",
                notes=tuple(notes),
            )
        session_id: str | None = None
        for event in events:
            if event.get("type") == "thread.started":
                thread_id = event.get("thread_id")
                if isinstance(thread_id, str) and thread_id:
                    session_id = thread_id
                    break
        failed = [e for e in events if e.get("type") == "turn.failed"]
        completed = any(e.get("type") == "turn.completed" for e in events)
        errors = [e for e in events if e.get("type") == "error"]
        if failed or not completed or _exit_failed(exit_code):
            message: str | None = None
            if failed:
                error = failed[-1].get("error")
                if isinstance(error, dict):
                    message = error.get("message")
            if (not isinstance(message, str) or not message) and errors:
                message = errors[-1].get("message")
            if not isinstance(message, str) or not message:
                message = _exit_or_incomplete(exit_code)
            return NativeResult(
                session_id=session_id,
                structured=None,
                native_error=message,
                notes=tuple(notes),
            )
        path = spec.raw_dir / LAST_MESSAGE
        try:
            raw = path.read_text()
        except OSError:
            notes.append("no structured result")
            return NativeResult(
                session_id=session_id, structured=None, notes=tuple(notes)
            )
        try:
            structured = json.loads(raw.strip())
        except Exception:
            notes.append("structured result is not a JSON object")
            return NativeResult(
                session_id=session_id, structured=None, notes=tuple(notes)
            )
        if not isinstance(structured, dict):
            notes.append("structured result is not a JSON object")
            return NativeResult(
                session_id=session_id, structured=None, notes=tuple(notes)
            )
        structured_dict: dict[str, Any] = structured
        return NativeResult(
            session_id=session_id, structured=structured_dict, notes=tuple(notes)
        )

    def attach_command(self, session_id: str, spec: LaunchSpec) -> list[str]:
        """Return the argv that reopens this session (SPEC §6.3)."""
        return ["codex", "resume", session_id]


def _parse_lines(text: str) -> tuple[list[dict[str, Any]], int]:
    """Split JSON lines output into events, counting skipped lines (SPEC §6.4)."""
    events: list[dict[str, Any]] = []
    skipped = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            skipped += 1
            continue
        try:
            value = json.loads(stripped)
        except Exception:
            skipped += 1
            continue
        if not isinstance(value, dict):
            skipped += 1
            continue
        events.append(value)
    return events, skipped


def _exit_failed(exit_code: int | None) -> bool:
    return isinstance(exit_code, int) and exit_code != 0


def _exit_or_incomplete(exit_code: int | None) -> str:
    if _exit_failed(exit_code):
        return f"exit code {exit_code}"
    return "turn did not complete"
