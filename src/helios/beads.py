"""Beads tracker access (SPEC §7.1 step 1, §7.5).

``Beads`` is the only module that calls ``bd``: every subprocess argv lives
here. ``FakeBeads`` offers the same interface in memory for tests. Write-back
carries the marker ``[<attempt_id>]`` (or ``[<attempt_id>#k]`` per line) and
is skipped when a comment with that kind and marker already exists, so a
crashed write-back can be replayed (SPEC §7.5).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


def decode_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """Decode metadata values that arrived JSON-encoded as strings (SPEC §7.1)."""
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            try:
                out[key] = json.loads(value)
                continue
            except (json.JSONDecodeError, ValueError):
                pass
        out[key] = value
    return out


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


@dataclass
class Bead:
    id: str
    title: str = ""
    description: str = ""
    kind: str = "impl"
    unit: str | None = None
    parent: str | None = None
    accept: str = ""
    files: list[str] = field(default_factory=list)
    test: str = ""
    author: str | None = None
    status: str = "open"
    metadata: dict[str, Any] = field(default_factory=dict)
    docs: list[str] = field(default_factory=list)
    memories: list[str] = field(default_factory=list)

    @classmethod
    def from_show(cls, payload: dict[str, Any]) -> Bead:
        """Build a Bead from one decoded `bd show` object."""
        metadata = decode_metadata(dict(payload.get("metadata") or {}))
        kind = str(metadata.get("kind") or _label_kind(payload.get("labels") or []) or "impl")
        accept = str(metadata.get("accept") or payload.get("acceptance_criteria") or "")
        return cls(
            id=str(payload["id"]),
            title=str(payload.get("title") or ""),
            description=str(payload.get("description") or ""),
            kind=kind,
            unit=_opt_str(metadata.get("unit")),
            parent=_opt_str(metadata.get("parent")),
            accept=accept,
            files=_as_list(metadata.get("files")),
            test=str(metadata.get("test") or ""),
            author=_opt_str(metadata.get("author")),
            status=str(payload.get("status") or "open"),
            metadata=metadata,
            docs=_as_list(metadata.get("docs")),
            memories=_as_list(metadata.get("memories")),
        )


def _opt_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _label_kind(labels: list[Any]) -> str | None:
    for label in labels:
        text = str(label)
        if text.startswith("kind:"):
            return text.split(":", 1)[1]
    return None


@dataclass
class Comment:
    id: str
    issue_id: str
    author: str
    text: str
    created_at: str = ""


def comment_kind(text: str) -> str:
    """First token before the colon: ``event``, ``learned``, ``question``, ..."""
    head = text.split(":", 1)[0].strip()
    return head.split()[-1] if head else ""


def comment_has(comments: list[Comment], kind: str, marker: str) -> bool:
    """True when a comment with that kind and marker already exists (SPEC §7.5)."""
    return any(
        comment_kind(c.text) == kind and marker in c.text for c in comments
    )


@dataclass(frozen=True)
class Writeback:
    """The write-back plan for one attempt (SPEC §7.5)."""

    metadata: dict[str, str]
    comments: tuple[str, ...]
    close: bool
    close_reason: str
    run_state: str | None


def plan_writeback(
    *,
    attempt_id: str,
    bead_kind: str,
    harness: str,
    session_id: str | None,
    worktree: str,
    attempt: int,
    execution_status: str,
    verdict: str | None,
    output_commit: str | None,
    report_status: str | None,
    report_summary: str,
    learned: list[str],
    missing_context: list[str],
    followups: list[str],
    question: str | None,
    checks_passed: bool,
) -> Writeback:
    """Build the metadata writes, comments and close decision (SPEC §7.5)."""
    metadata: dict[str, str] = {
        "harness": harness,
        "worktree": worktree,
        "attempt": str(attempt),
        "execution_status": execution_status,
    }
    if session_id is not None:
        metadata["session"] = f"{harness}:{session_id}"
    if verdict is not None:
        metadata["verdict"] = verdict
    if output_commit is not None:
        metadata["output_commit"] = output_commit
    comments = [f"event: [{attempt_id}] {execution_status} {report_status} {report_summary}"]
    for i, line in enumerate(learned, 1):
        comments.append(f"learned: [{attempt_id}#{i}] {line}")
    for i, line in enumerate(missing_context, 1):
        comments.append(f"missing_context: [{attempt_id}#{i}] {line}")
    for i, line in enumerate(followups, 1):
        comments.append(f"followup: [{attempt_id}#{i}] {line}")
    if question:
        comments.append(f"question: [{attempt_id}] {question}")
    close = should_close(
        bead_kind,
        execution_status=execution_status,
        report_status=report_status,
        checks_passed=checks_passed,
        verdict=verdict,
    )
    run_state = None if close else run_state_for(report_status, execution_status)
    return Writeback(
        metadata=metadata,
        comments=tuple(comments),
        close=close,
        close_reason=report_summary,
        run_state=run_state,
    )


def should_close(
    bead_kind: str,
    *,
    execution_status: str,
    report_status: str | None,
    checks_passed: bool,
    verdict: str | None,
) -> bool:
    """Close rule of SPEC §7.5: done plus passing checks, plus verified for verify."""
    if execution_status != "completed" or report_status != "done" or not checks_passed:
        return False
    if bead_kind.startswith("verify"):
        return verdict == "verified"
    return True


def run_state_for(report_status: str | None, execution_status: str) -> str:
    """Map a non-closing outcome to the ``bd set-state run=`` value (SPEC §7.5)."""
    if report_status == "blocked":
        return "blocked"
    if report_status in ("needs_input", "needs_review", "partial"):
        return "waiting"
    if execution_status in ("completed", "missing_output", "invalid_output"):
        return "waiting"
    return "failed"


class BeadsLike(Protocol):
    def show(self, bead_id: str) -> Bead: ...
    def comments(self, bead_id: str) -> list[Comment]: ...
    def add_comment(self, bead_id: str, text: str) -> None: ...
    def set_metadata(self, bead_id: str, metadata: dict[str, str]) -> None: ...
    def close(self, bead_id: str, reason: str) -> None: ...
    def set_state(self, bead_id: str, dimension: str, value: str, reason: str) -> None: ...


class Beads:
    """The only module that calls ``bd`` (SPEC §7.1 step 1)."""

    def __init__(self, cwd: Path, binary: str = "bd") -> None:
        self.cwd = cwd
        self.binary = binary

    def _run(self, argv: list[str]) -> str:
        proc = subprocess.run(
            [self.binary, *argv],
            cwd=self.cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"bd {' '.join(argv)} failed: {proc.stderr.strip()}")
        return proc.stdout

    def show(self, bead_id: str) -> Bead:
        out = self._run(["show", bead_id, "--json"])
        payloads = json.loads(out)
        if not payloads:
            raise KeyError(f"bead {bead_id} not found")
        return Bead.from_show(payloads[0])

    def comments(self, bead_id: str) -> list[Comment]:
        out = self._run(["comments", bead_id, "--json"])
        return [Comment(**c) for c in json.loads(out or "[]")]

    def add_comment(self, bead_id: str, text: str) -> None:
        self._run(["comment", bead_id, text])

    def set_metadata(self, bead_id: str, metadata: dict[str, str]) -> None:
        argv = ["update", bead_id]
        for key, value in metadata.items():
            argv += ["--set-metadata", f"{key}={value}"]
        self._run(argv)

    def close(self, bead_id: str, reason: str) -> None:
        self._run(["close", bead_id, "--reason", reason])

    def set_state(self, bead_id: str, dimension: str, value: str, reason: str) -> None:
        self._run(["set-state", bead_id, f"{dimension}={value}", "--reason", reason])


class FakeBeads:
    """In-memory ``Beads`` for tests, same interface."""

    def __init__(self, beads: list[Bead] | None = None) -> None:
        self.beads: dict[str, Bead] = {b.id: b for b in beads or []}
        self._comments: dict[str, list[Comment]] = {}
        self.closed: dict[str, str] = {}
        self.states: dict[str, dict[str, str]] = {}
        self.argv_log: list[list[str]] = []

    def show(self, bead_id: str) -> Bead:
        return self.beads[bead_id]

    def comments(self, bead_id: str) -> list[Comment]:
        return list(self._comments.get(bead_id, []))

    def add_comment(self, bead_id: str, text: str) -> None:
        self.argv_log.append(["comment", bead_id, text])
        existing = self._comments.setdefault(bead_id, [])
        existing.append(
            Comment(id=f"c{len(existing)}", issue_id=bead_id, author="helios", text=text)
        )

    def set_metadata(self, bead_id: str, metadata: dict[str, str]) -> None:
        self.argv_log.append(["update", bead_id, dict(metadata)])  # type: ignore[list-item]
        self.beads[bead_id].metadata.update(metadata)

    def close(self, bead_id: str, reason: str) -> None:
        self.argv_log.append(["close", bead_id, reason])
        self.closed[bead_id] = reason

    def set_state(self, bead_id: str, dimension: str, value: str, reason: str) -> None:
        self.argv_log.append(["set-state", bead_id, dimension, value, reason])
        self.states.setdefault(bead_id, {})[dimension] = value


def apply_writeback(beads: BeadsLike, bead_id: str, plan: Writeback) -> int:
    """Apply a write-back plan, skipping comments whose kind and marker exist.

    Returns the number of comments added.
    """
    beads.set_metadata(bead_id, plan.metadata)
    existing = beads.comments(bead_id)
    added = 0
    for text in plan.comments:
        kind = comment_kind(text)
        match = re.search(r"\[[^\]]+\]", text)
        marker = match.group(0) if match else text
        if comment_has(existing, kind, marker):
            continue
        beads.add_comment(bead_id, text)
        existing = beads.comments(bead_id)
        added += 1
    if plan.close:
        beads.close(bead_id, plan.close_reason)
    elif plan.run_state is not None:
        beads.set_state(bead_id, "run", plan.run_state, plan.close_reason)
    return added
