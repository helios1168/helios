"""Claude Code harness adapter (SPEC §6.3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from helios.harness.base import Harness, LaunchSpec, NativeResult


class ClaudeAdapter(Harness):
    """Adapter for the Claude Code CLI (SPEC §6.3)."""

    name = "claude"

    def argv(self, spec: LaunchSpec) -> list[str]:
        """Build argv for a fresh or resumed turn (SPEC §6.3)."""
        schema_text = spec.report_schema_path.read_text()
        out = [
            "claude",
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            schema_text,
            "--permission-mode",
            "bypassPermissions",
        ]
        if spec.model:
            out += ["--model", spec.model]
        if spec.effort:
            out += ["--effort", spec.effort]
        if spec.resume_session:
            out += ["--resume", spec.resume_session]
        out += list(spec.extra_args)
        out.append(spec.prompt)
        return out

    def stdin_text(self, spec: LaunchSpec) -> str | None:
        """The prompt travels in argv for claude (SPEC §6.3)."""
        return None

    def attach_command(self, session_id: str, spec: LaunchSpec) -> list[str]:
        """Return the argv that reopens this session (SPEC §6.3)."""
        return ["claude", "--resume", session_id]

    def parse(
        self, spec: LaunchSpec, exit_code: int | None, stdout_path: Path
    ) -> NativeResult:
        """Parse the single JSON object stdout (SPEC §6.4)."""
        try:
            data = stdout_path.read_bytes()
        except OSError:
            return NativeResult(session_id=None, structured=None, native_error="no stdout")
        try:
            obj = json.loads(data.decode("utf-8").strip())
        except Exception:
            return NativeResult(session_id=None, structured=None, native_error="invalid JSON")
        if not isinstance(obj, dict):
            return NativeResult(session_id=None, structured=None, native_error="invalid JSON")
        session_id = obj.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            session_id = None
        if obj.get("is_error") is True or _exit_failed(exit_code):
            result = obj.get("result")
            if not isinstance(result, str) or not result:
                result = _exit_or_incomplete(exit_code)
            return NativeResult(
                session_id=session_id, structured=None, native_error=result
            )
        structured: Any = obj.get("structured_output")
        if "structured_output" not in obj:
            return NativeResult(
                session_id=session_id,
                structured=None,
                notes=("no structured result",),
            )
        if not isinstance(structured, dict):
            return NativeResult(
                session_id=session_id,
                structured=None,
                notes=("structured result is not a JSON object",),
            )
        return NativeResult(session_id=session_id, structured=structured)


def _exit_failed(exit_code: int | None) -> bool:
    return isinstance(exit_code, int) and exit_code != 0


def _exit_or_incomplete(exit_code: int | None) -> str:
    if _exit_failed(exit_code):
        return f"exit code {exit_code}"
    return "turn did not complete"
