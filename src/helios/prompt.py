"""Prompt assembly (SPEC §7.2).

``assemble`` returns one string with sections in order, each under a ``##``
heading: Role, Contract, Bead, Docs, Memories, Attempt. Sections 4 and 5
together are capped at ``memory.inject_cap_bytes``; truncation appends a line
naming what was cut. The prompt never names the harness, so the bytes are
identical across harnesses.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path

BEAD_FIELDS = ("id", "title", "description", "kind", "unit", "accept", "files", "test")


def strip_front_matter(text: str) -> str:
    """Remove a leading ``---`` YAML front matter block (the Role skill has one)."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    rest = text[end + len("\n---") :]
    return rest.lstrip("\n")


def section(markdown: str, heading: str) -> str:
    """Extract the section starting at a heading line, including subsections.

    The section ends at the next heading of the same or higher level, or at
    the end of the document. Raises KeyError when the heading is absent.
    """
    lines = markdown.splitlines()
    match = re.fullmatch(r"(#+)\s+(.*)", heading.strip())
    if match:
        level, title = len(match.group(1)), match.group(2).strip()
    else:
        level, title = 2, heading.strip()
    start = None
    for i, line in enumerate(lines):
        m = re.fullmatch(r"(#+)\s+(.*)", line.strip())
        if m and len(m.group(1)) == level and m.group(2).strip() == title:
            start = i
            break
    if start is None:
        raise KeyError(f"heading {heading!r} not found")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        m = re.fullmatch(r"(#+)\s+(.*)", lines[i].strip())
        if m and len(m.group(1)) <= level:
            end = i
            break
    return "\n".join(lines[start:end]).rstrip() + "\n"


def contract_text(agents_md: str) -> str:
    """The ``## Worker contract`` section of AGENTS.md, verbatim."""
    return section(agents_md, "## Worker contract")


def read_doc(hub: Path, entry: str) -> tuple[str, str]:
    """Read a docs entry, following ``path#heading`` extraction (SPEC §7.2)."""
    path_text, sep, heading = entry.partition("#")
    text = (hub / path_text).read_text()
    if sep:
        text = section(text, heading)
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
    capped_docs, capped_memories, cut = _apply_cap(entries, mem_parts, inject_cap_bytes)
    docs_text = "\n\n".join(capped_docs)
    if cut:
        docs_text += f"\n\n[truncated for inject cap: {', '.join(cut)}]"
    memories_text = "\n\n".join(capped_memories)
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


def _apply_cap(
    docs: list[tuple[str, str]], memories: list[tuple[str, str]], cap: int
) -> tuple[list[str], list[str], list[str]]:
    """Cap docs plus memories at ``cap`` bytes, naming what was cut."""
    texts = [text for _, text in docs] + [text for _, text in memories]
    if _size(texts) <= cap:
        return [t for _, t in docs], [t for _, t in memories], []
    kept_memories, cut_memories = _fit_named(memories, cap)
    remaining = cap - _size([t for _, t in kept_memories])
    kept_docs, cut_docs = _fit_named(docs, max(remaining, 0))
    return (
        [t for _, t in kept_docs],
        [t for _, t in kept_memories],
        [n for n, _ in cut_docs] + [n for n, _ in cut_memories],
    )


def _size(parts: list[str]) -> int:
    return sum(len(p.encode("utf-8")) + 2 for p in parts)


def _fit_named(
    parts: list[tuple[str, str]], cap: int
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    kept: list[tuple[str, str]] = []
    used = 0
    for i, (name, part) in enumerate(parts):
        cost = len(part.encode("utf-8")) + 2
        if used + cost > cap:
            if not kept:
                kept.append((name, _truncate_bytes(part, cap)))
                return kept, [(name, part)] + parts[i + 1 :]
            return kept, parts[i:]
        used += cost
        kept.append((name, part))
    return kept, []


def _truncate_bytes(text: str, cap: int) -> str:
    raw = text.encode("utf-8")[: max(cap, 0)]
    return raw.decode("utf-8", errors="ignore")
