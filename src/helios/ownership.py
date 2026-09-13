"""Ownership of changed paths (SPEC §7.4).

Changed paths are the tracked changes since ``base_commit`` plus untracked
files. Each path must match a ``files`` glob (a directory entry covers
everything below it) or start with a prefix in ``project.always_allowed``.
Verify kinds may touch only ``<verify_artifacts>/<unit>/``. Always rejected,
whatever the globs say: ``.beads/``, ``.helios/``, ``.agents/``, the memory
export directory, and anything matching ``project.confidential``.
"""

from __future__ import annotations

import fnmatch
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


def _run_git(worktree: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=worktree, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def changed_paths(worktree: Path, base_commit: str) -> list[str]:
    """Tracked changes since ``base_commit`` plus untracked files."""
    tracked = _run_git(worktree, "diff", "--name-only", base_commit).splitlines()
    untracked = _run_git(
        worktree, "ls-files", "--others", "--exclude-standard"
    ).splitlines()
    return sorted({p for p in [*tracked, *untracked] if p})


def _is_prefix_match(path: str, prefix: str) -> bool:
    return path == prefix.rstrip("/") or path.startswith(prefix)


def _glob_match(path: str, pattern: str) -> bool:
    if pattern.endswith("/"):
        return _is_prefix_match(path, pattern)
    if not any(ch in pattern for ch in "*?["):
        return path == pattern or path.startswith(pattern + "/")
    if pattern.endswith("/*"):
        stem = pattern[:-2]
        if _is_prefix_match(path, stem + "/"):
            return True
    return fnmatch.fnmatchcase(path, pattern)


def _matches_any(path: str, patterns: list[str] | tuple[str, ...]) -> bool:
    return any(_glob_match(path, p) for p in patterns)


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
) -> OwnershipResult:
    """Sort the changed paths into allowed and rejected (SPEC §7.4)."""
    rejected_prefixes = (*ALWAYS_REJECTED, memory_export_dir.rstrip("/") + "/")
    allowed: list[str] = []
    rejected: list[str] = []
    for path in changed_paths(worktree, base_commit):
        if any(_is_prefix_match(path, prefix) for prefix in rejected_prefixes):
            rejected.append(path)
            continue
        if _matches_any(path, list(confidential)):
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
