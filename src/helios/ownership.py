"""Ownership of changed paths (SPEC §7.4).

Changed paths are the tracked changes since ``base_commit`` (renames listed
on both sides) plus untracked files, all NUL separated so non-ASCII paths
stay unquoted. Each path must match a ``files`` entry or start with a prefix
in ``project.always_allowed``. A ``files`` entry ending in ``/`` covers
everything below that directory. Any other entry is a glob matched against
the whole path: ``*`` and ``?`` never match ``/``, ``**`` matches any number
of path segments, ``[...]`` is a character class that never matches ``/`` and
is negated by a leading ``!``. So ``src/*.py`` owns ``src/a.py`` but not
``src/sub/a.py``. Verify kinds may touch only
``<verify_artifacts>/<unit>/``. Always rejected, whatever the globs say:
``.beads/``, ``.helios/``, ``.agents/``, the memory export directory, and
anything matching ``project.confidential``. A confidential glob without ``/``
matches the last path segment at any depth, so ``*.pem`` matches
``keys/a.pem``. An untracked symlink whose path matches a
``project.link_into_worktrees`` glob is not a change.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ALWAYS_REJECTED = (".beads/", ".helios/", ".agents/")


@dataclass(frozen=True)
class OwnershipResult:
    allowed: tuple[str, ...] = ()
    rejected: tuple[str, ...] = field(default_factory=tuple)
    detail: str = ""

    @property
    def passed(self) -> bool:
        return not self.rejected


def _run_git(worktree: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", *args], cwd=worktree, capture_output=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.decode().strip()}")
    return proc.stdout


def changed_paths(
    worktree: Path, base_commit: str, *, link_into_worktrees: tuple[str, ...] = ()
) -> list[str]:
    """Tracked changes since ``base_commit`` plus untracked files (SPEC §7.4).

    An untracked symlink whose path matches a ``link_into_worktrees`` glob is
    a linked environment file, not a change.
    """
    tracked = _run_git(worktree, "diff", "--name-only", "--no-renames", "-z", base_commit)
    untracked = _run_git(worktree, "ls-files", "--others", "--exclude-standard", "-z")
    paths = {p.decode("utf-8") for p in tracked.split(b"\0") if p}
    for raw in untracked.split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8")
        if (worktree / path).is_symlink() and _matches_any(path, link_into_worktrees):
            continue
        paths.add(path)
    return sorted(paths)


def glob_to_regex(pattern: str) -> str:
    """Translate a SPEC §7.4 glob to a regex string (``*`` never crosses ``/``)."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern[i + 1 : i + 2] == "*":
                out.append("\x00")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        elif char == "[":
            close = pattern.find("]", i + 1)
            inner = pattern[i + 1 : close] if close != -1 else ""
            if close == -1 or inner in ("", "!"):
                out.append("\\[")
                i += 1
            else:
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                out.append(f"(?![/])[{inner}]")
                i = close + 1
        else:
            out.append(re.escape(char))
            i += 1
    text = "".join(out)
    text = text.replace("\x00/", "(?:.*/)?")
    return text.replace("\x00", ".*")


def glob_match(path: str, pattern: str) -> bool:
    """Match a whole path against a SPEC §7.4 glob."""
    return re.fullmatch(glob_to_regex(pattern), path) is not None


def _is_prefix_match(path: str, prefix: str) -> bool:
    return path == prefix.rstrip("/") or path.startswith(prefix)


def _matches_any(path: str, patterns: list[str] | tuple[str, ...]) -> bool:
    for entry in patterns:
        if entry.endswith("/"):
            if _is_prefix_match(path, entry):
                return True
        elif glob_match(path, entry):
            return True
    return False


def _matches_confidential(path: str, confidential: tuple[str, ...]) -> bool:
    """Confidential match; a glob without ``/`` hits the last segment (SPEC §7.4)."""
    if _matches_any(path, list(confidential)):
        return True
    last = path.rsplit("/", 1)[-1]
    return any("/" not in entry and glob_match(last, entry) for entry in confidential)


def check(
    *,
    worktree: Path,
    base_commit: str,
    files: list[str],
    always_allowed: tuple[str, ...] = ("tests/",),
    kind: str = "impl",
    verify_artifacts: str = "tools/verify",
    unit: str | None = None,
    confidential: tuple[str, ...] = (),
    memory_export_dir: str = ".helios/memories",
    link_into_worktrees: tuple[str, ...] = (),
) -> OwnershipResult:
    """Sort the changed paths into allowed and rejected (SPEC §7.4)."""
    rejected_prefixes = (*ALWAYS_REJECTED, memory_export_dir.rstrip("/") + "/")
    allowed: list[str] = []
    rejected: list[str] = []
    for path in changed_paths(worktree, base_commit, link_into_worktrees=link_into_worktrees):
        if any(_is_prefix_match(path, prefix) for prefix in rejected_prefixes):
            rejected.append(path)
            continue
        if _matches_confidential(path, confidential):
            rejected.append(path)
            continue
        if kind.startswith("verify"):
            scope = f"{verify_artifacts.rstrip('/')}/{unit}/" if unit else None
            if scope is not None and _is_prefix_match(path, scope):
                allowed.append(path)
            else:
                rejected.append(path)
            continue
        if _matches_any(path, files) or any(
            _is_prefix_match(path, prefix) for prefix in always_allowed
        ):
            allowed.append(path)
        else:
            rejected.append(path)
    detail = f"rejected: {', '.join(rejected)}" if rejected else "all paths allowed"
    return OwnershipResult(allowed=tuple(allowed), rejected=tuple(rejected), detail=detail)
