"""Prompt assembly (SPEC §7.2).

``assemble`` returns one string with sections in order, each under a ``##``
heading: Role, Contract, Bead, Docs, Memories, Attempt. A bare docs ``path``
includes the whole file; ``path#key`` includes the first heading of any level
whose text equals ``key`` or whose first word equals ``key``. Sections 4 and 5
together are capped at ``memory.inject_cap_bytes`` UTF-8 bytes, never splitting
a character; truncation appends a line naming what was cut, and that line
counts within the cap. The prompt never names the harness, so the bytes are
identical across harnesses.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

BEAD_FIELDS = ("id", "title", "description", "kind", "unit", "accept", "files", "test")

_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*$")


def strip_front_matter(text: str) -> str:
    """Remove a leading ``---`` YAML front matter block (the Role skill has one)."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    rest = text[end + len("\n---") :]
    return rest.lstrip("\n")


def _is_fence(line: str) -> str | None:
    """The fence marker opening or closing on this line, else None (SPEC §7.2)."""
    stripped = line.strip()
    if stripped.startswith("```"):
        return "`"
    if stripped.startswith("~~~"):
        return "~"
    return None


def _headings(markdown: str) -> list[tuple[int, str, str, int]]:
    """(line index, level, text, first word) for each heading outside fences."""
    found = []
    fence: str | None = None
    for i, line in enumerate(markdown.splitlines()):
        marker = _is_fence(line)
        if marker is not None:
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue
        match = _HEADING.match(line)
        if match:
            text = match.group(2)
            words = text.split()
            found.append((i, len(match.group(1)), text, words[0] if words else ""))
    return found


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
    entries = [(entry, f"### {entry}\n\n{read_doc(hub, entry)[1].strip()}") for entry in docs]
    mem_parts = [(key, f"### {key}\n\n{value.strip()}") for key, value in memories.items()]
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


def _apply_cap(
    docs: list[tuple[str, str]], memories: list[tuple[str, str]], cap: int
) -> tuple[list[str], list[str], str]:
    """Cap docs plus memories at ``cap`` UTF-8 bytes, note included.

    Returns the kept doc texts, the kept memory texts, and the truncation
    note (empty when nothing was cut). Never splits a UTF-8 character.
    """
    kept_docs, kept_mems, cut = _fit_all(docs, memories, cap)
    if not cut:
        return [t for _, t in kept_docs], [t for _, t in kept_mems], ""
    while True:
        note = _note_text(cut)
        room = cap - _blen(note) - 2
        if room < 0:
            if _blen(_GENERIC_NOTE) + 2 > cap:
                return [], [], _truncate_bytes(_GENERIC_NOTE, cap)
            return [], [], _GENERIC_NOTE
        kept_docs, kept_mems, new_cut = _fit_all(docs, memories, room)
        if set(new_cut) == set(cut):
            return [t for _, t in kept_docs], [t for _, t in kept_mems], note
        cut = new_cut


_GENERIC_NOTE = "[truncated for inject cap]"


def _fit_all(
    docs: list[tuple[str, str]], memories: list[tuple[str, str]], budget: int
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[str]]:
    kept_mems, cut_mems = _fit_named(memories, max(budget, 0))
    rest = max(budget - _blen(_join(kept_mems)), 0)
    kept_docs, cut_docs = _fit_named(docs, rest)
    cut = [n for n, _ in cut_docs] + [n for n, _ in cut_mems]
    return kept_docs, kept_mems, cut


def _join(parts: list[tuple[str, str]]) -> str:
    return "\n\n".join(text for _, text in parts)


def _blen(text: str) -> int:
    return len(text.encode("utf-8"))


def _fit_named(
    parts: list[tuple[str, str]], budget: int
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    kept: list[tuple[str, str]] = []
    used = 0
    for i, (name, part) in enumerate(parts):
        cost = _blen(part) + (2 if kept else 0)
        if used + cost > budget:
            if not kept:
                kept.append((name, _truncate_bytes(part, budget)))
                return kept, [(name, part)] + parts[i + 1 :]
            return kept, parts[i:]
        used += cost
        kept.append((name, part))
    return kept, []


def _truncate_bytes(text: str, budget: int) -> str:
    raw = text.encode("utf-8")[: max(budget, 0)]
    return raw.decode("utf-8", errors="ignore")
