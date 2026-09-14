"""opencode harness adapter (SPEC §6.3)."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from helios import jsonio
from helios.harness.base import Harness, LaunchSpec, NativeResult

STRIP_CHARS = " \t\r\n"


class OpencodeAdapter(Harness):
    """Adapter for opencode (SPEC §6.3)."""

    name = "opencode"

    def argv(self, spec: LaunchSpec) -> list[str]:
        """Build argv for a fresh or resumed turn (SPEC §6.3)."""
        out = [
            "opencode",
            "run",
            "--format",
            "json",
            "--dir",
            str(spec.worktree),
            "--title",
            f"{spec.bead}#{spec.attempt}",
        ]
        if spec.model:
            out += ["-m", spec.model]
        if spec.effort:
            out += ["--variant", spec.effort]
        if spec.server_url:
            out += ["--attach", spec.server_url]
        if spec.resume_session:
            out += ["-s", spec.resume_session]
        out.append("--auto")
        out += list(spec.extra_args)
        out.append(spec.prompt)
        return out

    def stdin_text(self, spec: LaunchSpec) -> str | None:
        """The prompt travels in argv for opencode (SPEC §6.3)."""
        return None

    def parse(
        self, spec: LaunchSpec, exit_code: int | None, stdout_path: Path
    ) -> NativeResult:
        """Parse the JSON lines stdout (SPEC §6.4)."""
        data = _read_bytes(stdout_path)
        if data is None:
            return NativeResult(session_id=None, structured=None, native_error="no stdout")
        events, skipped = _parse_lines(data)
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
            value = event.get("sessionID")
            if isinstance(value, str) and value:
                session_id = value
                break
        errors = [e for e in events if e.get("type") == "error"]
        if errors or events[-1].get("type") != "step_finish" or _exit_failed(exit_code):
            message: str | None = None
            name: str | None = None
            if errors:
                error = errors[0].get("error")
                if isinstance(error, dict):
                    name = error.get("name")
                    data = error.get("data")
                    if isinstance(data, dict):
                        message = data.get("message")
            if not isinstance(message, str) or not message:
                message = name if isinstance(name, str) and name else None
            if not isinstance(message, str) or not message:
                message = _exit_or_incomplete(exit_code)
            return NativeResult(
                session_id=session_id,
                structured=None,
                native_error=message,
                notes=tuple(notes),
            )
        return NativeResult(
            session_id=session_id, structured=None, notes=tuple(notes)
        )

    def attach_command(self, session_id: str, spec: LaunchSpec) -> list[str]:
        """Return the argv that reopens this session (SPEC §6.3)."""
        if spec.server_url:
            return [
                "opencode",
                "attach",
                spec.server_url,
                "--dir",
                str(spec.worktree),
                "-s",
                session_id,
            ]
        return ["opencode", str(spec.worktree), "-s", session_id]


def _parse_lines(data: bytes) -> tuple[list[dict[str, Any]], int]:
    """Split stdout bytes on newline only and decode each line (SPEC §6.4)."""
    pieces = data.split(b"\n")
    if pieces and pieces[-1] == b"":
        pieces.pop()
    events: list[dict[str, Any]] = []
    skipped = 0
    for piece in pieces:
        try:
            line = piece.decode("utf-8")
        except UnicodeDecodeError:
            skipped += 1
            continue
        stripped = line.strip(STRIP_CHARS)
        if not stripped:
            skipped += 1
            continue
        try:
            value = jsonio.loads(stripped)
        except Exception:
            skipped += 1
            continue
        if not isinstance(value, dict):
            skipped += 1
            continue
        events.append(value)
    return events, skipped


def _read_bytes(path: Path) -> bytes | None:
    """Read a regular file, else None (SPEC §6.4)."""
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return None
        return path.read_bytes()
    except (OSError, ValueError):
        return None


def _exit_failed(exit_code: int | None) -> bool:
    return isinstance(exit_code, int) and exit_code != 0


def _exit_or_incomplete(exit_code: int | None) -> str:
    if _exit_failed(exit_code):
        return f"exit code {exit_code}"
    return "turn did not complete"
