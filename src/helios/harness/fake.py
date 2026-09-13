"""Fake harness for tests (SPEC §6.3).

Driven by the JSON file in env ``HELIOS_FAKE_SCRIPT``::

    {"exit_code": 0, "sleep_s": 0, "stdout": "...", "session_id": "s1",
     "report": {...} | null, "report_text": "..." | null,
     "ignore_sigint": false}

It writes ``report`` (or raw ``report_text``) to ``HELIOS_REPORT``, prints
``stdout``, sleeps, exits. ``python -m helios.harness.fake`` runs it.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

from helios.harness.base import LaunchSpec, NativeResult


def run_fake() -> int:
    """Entry point for ``python -m helios.harness.fake`` (SPEC §6.3)."""
    script_path = os.environ.get("HELIOS_FAKE_SCRIPT", "")
    report_path = os.environ.get("HELIOS_REPORT", "")
    script: dict = {}
    if script_path:
        try:
            script = json.loads(Path(script_path).read_text())
        except (OSError, json.JSONDecodeError):
            script = {}
    if script.get("ignore_sigint"):
        try:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        except (ValueError, OSError):
            pass
    if report_path:
        report = script.get("report", None)
        report_text = script.get("report_text", None)
        if report is not None or report_text is not None:
            target = Path(report_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if report is not None:
                target.write_text(json.dumps(report))
            else:
                target.write_text(str(report_text))
    stdout_text = script.get("stdout", "")
    if stdout_text:
        sys.stdout.write(str(stdout_text))
        if not str(stdout_text).endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()
    sleep_s = float(script.get("sleep_s", 0) or 0)
    if sleep_s > 0:
        end = time.monotonic() + sleep_s
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 0.05))
    return int(script.get("exit_code", 0) or 0)


class FakeHarness:
    """Test adapter with no native schema channel (SPEC §6.3)."""

    name = "fake"

    def argv(self, spec: LaunchSpec) -> list[str]:
        """``python -m helios.harness.fake`` plus any extra args."""
        return [sys.executable, "-m", "helios.harness.fake", *spec.extra_args]

    def stdin_text(self, spec: LaunchSpec) -> str | None:
        """The prompt travels in the assembled prompt file, not stdin."""
        return None

    def parse(
        self, spec: LaunchSpec, exit_code: int | None, stdout_path: Path
    ) -> NativeResult:
        """Read the session id from the script file; no structured channel.

        Never raises on malformed output: missing stdout or a nonzero exit
        becomes a ``native_error`` and helios classifies the outcome.
        """
        session_id: str | None = None
        try:
            raw = spec.env.get("HELIOS_FAKE_SCRIPT", "")
            if raw:
                script = json.loads(Path(raw).read_text())
                value = script.get("session_id")
                session_id = str(value) if value is not None else None
        except (OSError, json.JSONDecodeError, ValueError, AttributeError):
            session_id = None
        if not stdout_path.is_file():
            return NativeResult(
                session_id=session_id,
                structured=None,
                native_error="missing stdout",
            )
        if exit_code is None:
            return NativeResult(
                session_id=session_id, structured=None, native_error="no exit code"
            )
        if exit_code != 0:
            return NativeResult(
                session_id=session_id,
                structured=None,
                native_error=f"exit {exit_code}",
            )
        return NativeResult(session_id=session_id, structured=None)

    def attach_command(self, session_id: str, spec: LaunchSpec) -> list[str]:
        """No interactive session for the fake harness."""
        return [sys.executable, "-m", "helios.harness.fake"]


if __name__ == "__main__":
    raise SystemExit(run_fake())
