"""Bead worktrees (SPEC §7.3).

Path ``<hub>/<project.worktrees>/<bead>``, branch ``worktree-<bead>``. New
trees are added from ``main`` and locked; existing ones are reused and
refused when their branch differs. Passing ``again`` resets an existing tree to
its branch tip. ``link_into_worktrees`` glob matches are symlinked in.
``base_commit`` is the worktree HEAD before launch.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class WorktreeError(RuntimeError):
    """An existing worktree cannot be reused for this bead."""


@dataclass(frozen=True)
class WorktreeInfo:
    path: Path
    branch: str
    base_commit: str
    created: bool


def branch_name(bead: str) -> str:
    return f"worktree-{bead}"


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def current_branch(path: Path) -> str:
    return _git(path, "rev-parse", "--abbrev-ref", "HEAD")


def head_commit(path: Path) -> str:
    return _git(path, "rev-parse", "HEAD")


def prepare(
    *,
    hub: Path,
    bead: str,
    worktrees: str = ".claude/worktrees",
    link_into_worktrees: tuple[str, ...] = (),
    again: bool = False,
) -> WorktreeInfo:
    """Create or reuse the bead worktree and return it with ``base_commit``."""
    path = hub / worktrees / bead
    branch = branch_name(bead)
    created = False
    if os.path.lexists(path):
        if not path.is_dir() or _real(path) not in _registered(hub):
            hub_branch = _git(hub, "rev-parse", "--abbrev-ref", "HEAD")
            raise WorktreeError(
                f"worktree path {path} exists and is not a worktree"
                f" (hub is on branch {hub_branch!r})"
            )
        actual = current_branch(path)
        if actual != branch:
            raise WorktreeError(
                f"worktree {path} is on branch {actual!r}, expected {branch!r}"
            )
        if again:
            _git(path, "reset", "--hard")
            _git(path, "clean", "-fd", "-e", ".helios")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        _git(hub, "worktree", "add", str(path), "-b", branch, "main")
        _git(hub, "worktree", "lock", "--reason", "keep", str(path))
        created = True
    link_matches(hub, path, link_into_worktrees)
    return WorktreeInfo(path=path, branch=branch, base_commit=head_commit(path), created=created)


def _real(path: Path) -> str:
    return os.path.realpath(path)


def _registered(hub: Path) -> set[str]:
    """Real paths of the worktrees git knows about."""
    out = _git(hub, "worktree", "list", "--porcelain")
    paths = set()
    for line in out.splitlines():
        if line.startswith("worktree "):
            paths.add(_real(Path(line[len("worktree ") :])))
    return paths


def link_matches(hub: Path, worktree: Path, patterns: tuple[str, ...]) -> list[Path]:
    """Symlink each glob match from the hub into the worktree; skip absent ones."""
    linked: list[Path] = []
    for pattern in patterns:
        for match in sorted(hub.glob(pattern)):
            if not match.exists() and not match.is_symlink():
                continue
            rel = match.relative_to(hub)
            target = worktree / rel
            if target.exists() or target.is_symlink():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(match)
            linked.append(target)
    return linked
