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


def _decode_show_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """Undo bd's double-encoding of compound metadata values from `bd show`/`bd list`.

    `bd update --set-metadata` stores a list or dict as a literal JSON-text string (for
    example `files` becomes '["a.py"]'), so it needs a second decode to come back as the
    real value. Scalars set the same way already arrive as their real type from bd, and
    `Beads.create` stores every value with its real JSON type too, including plain strings
    such as "true" or "3" that must stay strings. Unlike `decode_metadata`, this only
    unwraps a string when the decoded result is a list or dict, so already-correct strings
    and scalars are never reinterpreted.
    """
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                decoded = None
            if isinstance(decoded, (list, dict)):
                out[key] = decoded
                continue
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
    labels: list[str] = field(default_factory=list)

    @classmethod
    def from_show(cls, payload: dict[str, Any]) -> Bead:
        """Build a Bead from one decoded `bd show` or `bd list` object."""
        labels = _as_list(payload.get("labels"))
        metadata = _decode_show_metadata(dict(payload.get("metadata") or {}))
        kind = str(metadata.get("kind") or _label_kind(labels) or "impl")
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
            labels=labels,
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
    """True when a comment starts with ``<kind>: <marker>`` (SPEC §7.5).

    A marker quoted later in the text does not count.
    """
    prefix = f"{kind}: {marker}"
    return any(c.text.startswith(prefix) for c in comments)


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
    def ready(self, *, labels: list[str] = []) -> list[Bead]: ...
    def add_label(self, bead_id: str, label: str) -> None: ...
    def gate_list(self) -> list[dict[str, Any]]: ...
    def gate_blocks(self, gate_id: str) -> list[str]: ...


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

    def create(
        self,
        title: str,
        *,
        labels: list[str],
        metadata: dict[str, Any],
        type: str = "task",
        description: str = "",
    ) -> str:
        """Create a bead and return its id (`bd create --metadata <json> --silent`).

        Metadata is passed as one JSON object so every value keeps its real JSON type on
        the way in; see `_decode_show_metadata` for how that survives the way back out.
        """
        argv = ["create", "--title", title, "--type", type, "--metadata", json.dumps(metadata)]
        if description:
            argv += ["--description", description]
        if labels:
            argv += ["--labels", ",".join(labels)]
        argv += ["--silent"]
        return self._run(argv).strip()

    def dep_add(self, blocked: str, blocker: str) -> None:
        """`bd dep add <blocked> <blocker>`: blocked depends on (is blocked by) blocker.

        bd itself treats adding an existing dependency again as a no-op (exit 0, same
        confirmation message), so there is nothing extra to check here.
        """
        self._run(["dep", "add", blocked, blocker])

    def list(self, *, labels: list[str] = [], status: str | None = None) -> list[Bead]:
        """Beads carrying every label in `labels`, via `bd list --json --label a,b -n 0`.

        `status` filters to that stored status; `None` passes `--all` so every status,
        closed included, comes back (bd's default listing hides closed beads).
        """
        argv = ["list", "--json", "-n", "0"]
        if labels:
            argv += ["--label", ",".join(labels)]
        if status is None:
            argv += ["--all"]
        else:
            argv += ["--status", status]
        out = self._run(argv)
        return [Bead.from_show(p) for p in json.loads(out or "[]")]

    def ready(self, *, labels: list[str] = []) -> list[Bead]:
        """Ready beads via `bd ready --json --label a --label b -n 0`.

        `--label` is repeated once per label (AND semantics: a bead must carry every
        one). `-n 0` asks for unlimited rows, since `bd ready` otherwise defaults to
        100 and would silently truncate a large ready set.
        """
        argv = ["ready", "--json", "-n", "0"]
        for label in labels:
            argv += ["--label", label]
        out = self._run(argv)
        return [Bead.from_show(p) for p in json.loads(out or "[]")]

    def add_label(self, bead_id: str, label: str) -> None:
        """`bd label add <bead> <label>`; adding a label the bead already has is a no-op."""
        self._run(["label", "add", bead_id, label])

    def gate_list(self) -> list[dict[str, Any]]:
        """Open gates via `bd gate list --json -n 0`, bd's objects unchanged."""
        out = self._run(["gate", "list", "--json", "-n", "0"])
        return json.loads(out or "[]")

    def gate_blocks(self, gate_id: str) -> list[str]:
        """Sorted ids of the beads `gate_id` blocks.

        Reads `bd show <gate> --json --include-dependents`. The field is
        `dependents`: a list of objects with `id` and `dependency_type`, found by
        creating a human gate blocking two beads in a temp repo (`bd gate create
        --blocks`, `bd dep add <bead> <gate>`) and inspecting the JSON. Only entries
        with `dependency_type == "blocks"` count.
        """
        out = self._run(["show", gate_id, "--json", "--include-dependents"])
        payloads = json.loads(out)
        if not payloads:
            raise KeyError(f"bead {gate_id} not found")
        dependents = payloads[0].get("dependents") or []
        return sorted(
            str(d["id"]) for d in dependents if d.get("dependency_type") == "blocks"
        )

    def remember(self, key: str, value: str) -> None:
        """`bd remember --key <key> -- <value>`.

        The `--` separator is required: without it, a value starting with `-` is parsed as
        a flag. Round-trip through `recall` was checked byte for byte with a real bd for a
        body ending in no newline, one newline, two newlines, a multi-line body, and a body
        starting with `-`: every one of them comes back unchanged.
        """
        self._run(["remember", "--key", key, "--", value])

    def recall(self, key: str) -> str | None:
        """`bd recall <key> --json`; `None` when the key has no memory.

        Reads `--json` rather than plain stdout: plain `bd recall` always prints the value
        with one newline appended, which would be indistinguishable from a value that
        itself ends in a newline. The JSON `value` field is the exact stored text.
        """
        proc = subprocess.run(
            [self.binary, "recall", key, "--json"],
            cwd=self.cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(proc.stdout)
        return payload["value"] if payload.get("found") else None

    def memories(self) -> dict[str, str]:
        """`bd memories --json`: every stored key to its exact value."""
        out = self._run(["memories", "--json"])
        payload = json.loads(out or "{}")
        payload.pop("schema_version", None)
        return payload


class FakeBeads:
    """In-memory ``Beads`` for tests, same interface."""

    def __init__(self, beads: list[Bead] | None = None) -> None:
        self.beads: dict[str, Bead] = {b.id: b for b in beads or []}
        self._comments: dict[str, list[Comment]] = {}
        self.closed: dict[str, str] = {}
        self.states: dict[str, dict[str, str]] = {}
        self.argv_log: list[list[str]] = []
        self.deps: dict[str, set[str]] = {}
        self._memories: dict[str, str] = {}
        self._next_id = 1

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

    def create(
        self,
        title: str,
        *,
        labels: list[str],
        metadata: dict[str, Any],
        type: str = "task",
        description: str = "",
    ) -> str:
        bead_id = f"fake-{self._next_id}"
        self._next_id += 1
        log_entry = ["create", title, list(labels), dict(metadata), type, description]
        self.argv_log.append(log_entry)  # type: ignore[arg-type]
        self.beads[bead_id] = Bead.from_show(
            {
                "id": bead_id,
                "title": title,
                "description": description,
                "status": "open",
                "labels": list(labels),
                "metadata": dict(metadata),
            }
        )
        return bead_id

    def dep_add(self, blocked: str, blocker: str) -> None:
        self.argv_log.append(["dep", "add", blocked, blocker])
        self.deps.setdefault(blocked, set()).add(blocker)

    def list(self, *, labels: list[str] = [], status: str | None = None) -> list[Bead]:
        wanted = set(labels)
        return [
            b
            for b in self.beads.values()
            if wanted.issubset(b.labels) and (status is None or b.status == status)
        ]

    def ready(self, *, labels: list[str] = []) -> list[Bead]:
        wanted = set(labels)

        def blocked(bead_id: str) -> bool:
            return any(
                self.beads[blocker].status != "closed"
                for blocker in self.deps.get(bead_id, set())
                if blocker in self.beads
            )

        return [
            b
            for b in self.beads.values()
            if wanted.issubset(b.labels) and b.status == "open" and not blocked(b.id)
        ]

    def add_label(self, bead_id: str, label: str) -> None:
        self.argv_log.append(["label", "add", bead_id, label])
        labels = self.beads[bead_id].labels
        if label not in labels:
            labels.append(label)

    def gate_list(self) -> list[dict[str, Any]]:
        return [
            {"id": b.id, "issue_type": "gate", "status": b.status}
            for b in self.beads.values()
            if b.metadata.get("issue_type") == "gate" and b.status == "open"
        ]

    def gate_blocks(self, gate_id: str) -> list[str]:
        return sorted(blocked for blocked, blockers in self.deps.items() if gate_id in blockers)

    def remember(self, key: str, value: str) -> None:
        self.argv_log.append(["remember", "--key", key, value])
        self._memories[key] = value

    def recall(self, key: str) -> str | None:
        return self._memories.get(key)

    def memories(self) -> dict[str, str]:
        return dict(self._memories)


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
