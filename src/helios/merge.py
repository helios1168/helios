"""Merge an implementation bead (SPEC section 12)."""

from __future__ import annotations

import fcntl
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from helios.beads import Bead, BeadsLike, comment_has
from helios.config import ProjectConfig
from helios.envelope import Envelope


class MergeError(RuntimeError):
    """A merge refusal or operational failure."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


CheckRunner = Callable[[str, Path], int]
HashFunction = Callable[[Bead], dict[str, str]]


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _git_output(cwd: Path, *args: str) -> str:
    proc = _git(cwd, *args)
    if proc.returncode:
        raise MergeError(proc.stderr.strip() or f"git {' '.join(args)} failed", 4)
    return proc.stdout.strip()


def _default_runner(command: str, cwd: Path) -> int:
    return subprocess.run(["bash", "-c", command], cwd=cwd, check=False).returncode


def _default_hashes(bead: Bead) -> dict[str, str]:
    """Return hashes for the bead's persisted input files."""
    hashes: dict[str, str] = {}
    for key, value in bead.metadata.get("input_hashes", {}).items():
        hashes[str(key)] = str(value)
    return hashes


def _comment(beads: BeadsLike, bead: str, marker: str, detail: str) -> None:
    text = f"merge: {marker} {detail}"
    if not comment_has(beads.comments(bead), "merge", marker):
        beads.add_comment(bead, text)


def _clean(path: Path) -> bool:
    return not _git_output(path, "status", "--porcelain", "--untracked-files=no")


def _verify_evidence(hub: Path, runs: str, impl: Bead, beads: BeadsLike, hashes: HashFunction) -> None:
    if impl.status != "closed":
        raise MergeError("impl bead is not closed")
    if not impl.unit:
        raise MergeError("impl bead has no unit")
    list_beads = cast(Callable[..., list[Bead]], getattr(beads, "list"))
    verifiers = [
        bead
        for bead in list_beads(labels=[f"unit:{impl.unit}"])
        if bead.parent == impl.id
    ]
    if not verifiers:
        raise MergeError("no verify bead evidence")
    for verifier in verifiers:
        if verifier.status != "closed" or verifier.metadata.get("verdict") != "verified":
            raise MergeError("verify evidence is not closed and verified")
        attempt = verifier.metadata.get("attempt")
        if attempt is None:
            raise MergeError("verify evidence has no attempt")
        path = hub / runs / verifier.id / f"attempt-{attempt}" / "envelope.json"
        if not path.is_file():
            raise MergeError("verify envelope is missing")
        try:
            envelope = Envelope.model_validate_json(path.read_text())
        except Exception as exc:
            raise MergeError(f"invalid verify envelope: {exc}") from exc
        if envelope.base_commit != impl.metadata.get("output_commit"):
            raise MergeError("verify envelope is stale")
        current = hashes(impl)
        recorded = envelope.input_hashes
        keys = set(current) | set(recorded)
        if any(key != "prompt" and current.get(key) != recorded.get(key) for key in keys):
            raise MergeError("verify envelope is stale")


def _remove_worktree(hub: Path, path: Path, branch: str) -> None:
    if path.exists() or path.is_symlink():
        if _git(hub, "worktree", "unlock", str(path)).returncode not in (0, 128):
            raise MergeError("worktree unlock failed", 4)
        proc = _git(hub, "worktree", "remove", "--force", str(path))
        if proc.returncode:
            raise MergeError(proc.stderr.strip() or "worktree removal failed", 4)
    if _git(hub, "show-ref", "--verify", f"refs/heads/{branch}").returncode == 0:
        proc = _git(hub, "branch", "-d", branch)
        if proc.returncode:
            raise MergeError(proc.stderr.strip() or "branch removal failed", 4)


def merge_bead(
    hub: Path,
    bead_id: str,
    *,
    project: ProjectConfig,
    beads: BeadsLike,
    check_runner: CheckRunner = _default_runner,
    input_hashes: HashFunction = _default_hashes,
    dry_run: bool = False,
) -> tuple[int, str]:
    """Integrate one closed implementation bead, returning ``(exit_code, message)``."""
    bead = beads.show(bead_id)
    runs_dir = hub / project.runs / bead_id
    lock_path = runs_dir / "lock"
    runs_dir.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("a+")
    try:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise MergeError("merge lock is held", 2) from exc
        _verify_evidence(hub, project.runs, bead, beads, input_hashes)
        worktree = hub / project.worktrees / bead_id
        branch_check = _git(hub, "symbolic-ref", "--short", "HEAD")
        if branch_check.returncode or branch_check.stdout.strip() != "main":
            raise MergeError("hub is not on main")
        if not worktree.is_dir() or not _clean(hub) or not _clean(worktree):
            raise MergeError("hub or worktree is dirty")
        branch = f"worktree-{bead_id}"
        merge_commit = bead.metadata.get("merge_commit")
        if merge_commit and _git(hub, "merge-base", "--is-ancestor", str(merge_commit), "main").returncode == 0:
            if dry_run:
                return 0, "would recover and remove worktree"
            _remove_worktree(hub, worktree, branch)
            return 0, "recovered"
        main_before = _git_output(hub, "rev-parse", "main")
        marker = f"[{bead_id}@{main_before}:"
        if dry_run:
            return 0, "would rebase, test, merge, push, and remove"
        beads.set_metadata(bead_id, {"merge_main_before": main_before})
        rebase = _git(worktree, "rebase", "main")
        if rebase.returncode:
            _git(worktree, "rebase", "--abort")
            _comment(beads, bead_id, marker + "conflict]", "conflict")
            raise MergeError("rebase conflict", 3)
        _comment(beads, bead_id, marker + "rebased]", "rebased")
        for name, command in (("test", project.test), ("typecheck", project.typecheck)):
            if command and check_runner(command, worktree) != 0:
                _comment(beads, bead_id, marker + "test-failed]", "test-failed")
                raise MergeError(f"{name} failed", 5)
        _comment(beads, bead_id, marker + "tested]", "tested")
        if _git_output(hub, "rev-parse", "main") != main_before:
            _comment(beads, bead_id, marker + "main-moved]", "main-moved")
            raise MergeError("main moved during checks", 3)
        commit = _git_output(worktree, "rev-parse", "HEAD")
        beads.set_metadata(bead_id, {"merge_commit": commit})
        merged = _git(hub, "merge", "--ff-only", branch)
        if merged.returncode:
            raise MergeError(merged.stderr.strip() or "fast-forward merge failed", 4)
        _comment(beads, bead_id, marker + "merged]", "merged")
        if "origin" in _git_output(hub, "remote").splitlines():
            pushed = _git(hub, "push", "origin", "main")
            if pushed.returncode:
                raise MergeError(pushed.stderr.strip() or "push failed", 4)
            _comment(beads, bead_id, marker + "pushed]", "pushed")
        _remove_worktree(hub, worktree, branch)
        _comment(beads, bead_id, marker + "removed]", "removed")
        return 0, "merged"
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
