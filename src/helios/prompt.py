"""Prompt assembly (SPEC §7.2).

``assemble`` returns one string with sections in order, each under a ``##``
heading: Role, Contract, Bead, Docs, Memories, Attempt. A bare docs ``path``
includes the whole file; ``path#key`` includes the first heading of any level
whose text equals ``key`` or whose first word equals ``key``. Sections 4 and 5
together are capped at ``memory.inject_cap_bytes`` UTF-8 bytes. The docs entries
followed by the memory values form one ordered list of items, each kept whole
or cut whole: helios keeps the longest prefix whose bytes, plus the note line
naming the cut items, fit within the cap. The prompt never names the harness,
so the bytes are identical across harnesses.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

BEAD_FIELDS = ("id", "title", "description", "kind", "unit", "accept", "files", "test")

_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def strip_front_matter(text: str) -> str:
    """Remove a leading ``---`` YAML front matter block (the Role skill has one)."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    rest = text[end + len("\n---") :]
    return rest.lstrip("\n")


def _headings(markdown: str) -> list[tuple[int, int, str, str]]:
    """(line index, level, text, first word) for each heading outside fences.

    A heading starts with 1 to 6 ``#`` followed by a space or the end of the
    line. A fence opens with 3 or more backticks or tildes and closes only on
    a line of the same character at least as long; an unclosed fence runs to
    the end of the file (SPEC §7.2).
    """
    found: list[tuple[int, int, str, str]] = []
    fence_char: str | None = None
    fence_len = 0
    for i, line in enumerate(markdown.splitlines()):
        fence = _FENCE.match(line)
        if fence is not None:
            run = fence.group(1)
            if fence_char is None:
                fence_char, fence_len = run[0], len(run)
            elif run[0] == fence_char and len(run) >= fence_len and not line[fence.end() :].strip():
                fence_char, fence_len = None, 0
            continue
        if fence_char is not None:
            continue
        match = _HEADING.match(line)
        if match:
            text = match.group(2) or ""
            words = text.split()
            found.append((i, len(match.group(1)), text, words[0] if words else ""))
    return found


def heading_lines(markdown: str) -> set[int]:
    """Line indexes holding a heading in the SPEC §7.2 sense."""
    return {i for i, _, _, _ in _headings(markdown)}


def model_section(markdown: str) -> str:
    """The ``## Model`` section of a unit file (SPEC §7.1 step 2).

    Starts at the first level 2 heading whose text is exactly ``Model`` (not
    a ``path#key`` first-word match) and runs to the next heading of level 1
    or 2. Raises KeyError when absent.
    """
    lines = markdown.splitlines()
    start = None
    for i, level, text, _first in _headings(markdown):
        if level == 2 and text == "Model":
            start = i
            break
    if start is None:
        raise KeyError("heading '## Model' not found")
    end = len(lines)
    for i, level, _text, _first in _headings(markdown):
        if i > start and level <= 2:
            end = i
            break
    return "\n".join(lines[start:end]).rstrip() + "\n"


def find_section(markdown: str, key: str) -> str:
    """The first heading whose text equals ``key`` or whose first word does.

    Runs to the next heading of the same or a higher level, so subsections
    are included. Raises KeyError when nothing matches.
    """
    lines = markdown.splitlines()
    start = None
    level = 0
    for i, lvl, text, first in _headings(markdown):
        if text == key or first == key:
            start, level = i, lvl
            break
    if start is None:
        raise KeyError(f"heading {key!r} not found")
    end = len(lines)
    for i, lvl, _text, _first in _headings(markdown):
        if i > start and lvl <= level:
            end = i
            break
    return "\n".join(lines[start:end]).rstrip() + "\n"


def contract_text(agents_md: str) -> str:
    """The Worker contract section of AGENTS.md, verbatim."""
    return find_section(agents_md, "Worker contract")


def read_doc(hub: Path, entry: str) -> tuple[str, str]:
    """Read a docs entry, following ``path#key`` extraction (SPEC §7.2)."""
    path_text, sep, key = entry.partition("#")
    text = (hub / path_text).read_text()
    if sep:
        text = find_section(text, key)
    return entry, text


def assemble(
    *,
    kind: str,
    skills_dir: Path,
    agents_path: Path,
    bead: Mapping[str, object],
    docs: list[str],
    hub: Path,
    memories: Mapping[str, str],
    worktree: Path,
    branch: str,
    attempt_id: str,
    report_path: Path,
    report_schema: str,
    inject_cap_bytes: int,
) -> str:
    """Assemble the worker prompt; identical bytes for any harness (SPEC §7.2)."""
    role = strip_front_matter((skills_dir / kind / "SKILL.md").read_text()).strip()
    contract = contract_text(agents_path.read_text()).strip()
    bead_json = json.dumps(
        {k: bead.get(k) for k in BEAD_FIELDS}, indent=2, sort_keys=True
    )
    entries = [
        (entry, f"### {entry}\n\n{read_doc(hub, entry)[1].strip('\n')}") for entry in docs
    ]
    mem_parts = [(key, f"### {key}\n\n{value.strip('\n')}") for key, value in memories.items()]
    capped_docs, capped_mems, note = _apply_cap(entries, mem_parts, inject_cap_bytes)
    memories_text = "\n\n".join(capped_mems)
    if note:
        memories_text = f"{memories_text}\n\n{note}" if memories_text else note
    docs_text = "\n\n".join(capped_docs)
    attempt = (
        f"worktree: {worktree}\n"
        f"branch: {branch}\n"
        f"attempt: {attempt_id}\n"
        f"report path: {report_path}\n\n"
        f"Report schema:\n{report_schema.strip()}\n"
    )
    parts = [
        f"## Role\n\n{role}",
        f"## Contract\n\n{contract}",
        f"## Bead\n\n{bead_json}",
        f"## Docs\n\n{docs_text}",
        f"## Memories\n\n{memories_text}",
        f"## Attempt\n\n{attempt}",
    ]
    return "\n\n".join(parts) + "\n"


def _note_text(names: list[str]) -> str:
    return f"[truncated for inject cap: {', '.join(names)}]"


_GENERIC_NOTE = "[truncated for inject cap]"


def _apply_cap(
    docs: list[tuple[str, str]], memories: list[tuple[str, str]], cap: int
) -> tuple[list[str], list[str], str]:
    """Cap docs plus memories at ``cap`` UTF-8 bytes, note included (SPEC §7.2).

    The docs entries followed by the memory values form one ordered list, each
    kept whole or cut whole. Tries prefix lengths from longest to shortest and
    keeps the first whose bytes, plus the note naming the cut items, fit. When
    even the empty prefix does not fit, the note is shortened at a character
    boundary to the cap. Returns kept doc texts, kept memory texts, and the
    note (empty when nothing was cut).
    """
    items = list(docs) + list(memories)
    for keep in range(len(items), -1, -1):
        kept = items[:keep]
        cut = [name for name, _ in items[keep:]]
        note = _note_text(cut) if cut else ""
        kept_docs = [t for _, t in kept[: len(docs)]]
        kept_mems = [t for _, t in kept[len(docs) :]]
        if _capped_bytes(kept_docs, kept_mems, note) <= cap:
            return kept_docs, kept_mems, note
    return [], [], _truncate_bytes(_note_text([n for n, _ in items]), cap)


def _capped_bytes(docs: list[str], mems: list[str], note: str) -> int:
    """Exact bytes of the Docs and Memories bodies plus the note (SPEC §7.2)."""
    docs_body = "\n\n".join(docs)
    mems_body = "\n\n".join(mems)
    if note and not mems_body:
        mems_body = note
    elif note:
        mems_body = f"{mems_body}\n\n{note}"
    return _blen(docs_body) + _blen(mems_body)


def _blen(text: str) -> int:
    return len(text.encode("utf-8"))


def _truncate_bytes(text: str, budget: int) -> str:
    raw = text.encode("utf-8")[: max(budget, 0)]
    return raw.decode("utf-8", errors="ignore")
