"""Merge an implementation bead (SPEC section 12)."""

from __future__ import annotations

import fcntl
import hashlib
import subprocess
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import TextIO, cast

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
    """Run git, decoding stdout/stderr as UTF-8 with `surrogateescape`, by hand
    from raw bytes rather than through a text-mode pipe.

    A manual decode never raises `UnicodeDecodeError` on a non-UTF-8 path or
    message (SPEC 12 item 2), and, unlike `subprocess.run(text=True)`, never
    translates a `\\r\\n` in the output.
    """
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    return subprocess.CompletedProcess(
        proc.args,
        proc.returncode,
        proc.stdout.decode("utf-8", "surrogateescape"),
        proc.stderr.decode("utf-8", "surrogateescape"),
    )


def _git_output(cwd: Path, *args: str) -> str:
    proc = _git(cwd, *args)
    if proc.returncode:
        raise MergeError(proc.stderr.strip() or f"git {' '.join(args)} failed", 4)
    return proc.stdout.strip()


class _DefaultRunner:
    """Runs a project check command with output captured, never inherited (SPEC 12 item 4).

    `last_output` holds the combined stdout and stderr of the most recent call, so a
    caller can report it on failure; a caller that only wants the exit code (most
    tests) can ignore it, and success discards it.
    """

    def __init__(self) -> None:
        self.last_output = ""

    def __call__(self, command: str, cwd: Path) -> int:
        proc = subprocess.run(
            ["bash", "-c", command],
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
        self.last_output = proc.stdout + proc.stderr
        return proc.returncode


_default_runner = _DefaultRunner()


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


def _clean(path: Path, ignore_beads: bool = True) -> bool:
    """True when `path` has no changes, dropping `.beads/` paths only when `ignore_beads`.

    Parses `git status --porcelain=v1 -z`: entries are NUL separated, and a rename or
    copy entry carries a second, NUL-terminated path (its original path), so a rename
    into `.beads/` from elsewhere still counts as a change.
    """
    proc = _git(path, "status", "--porcelain=v1", "-z", "--untracked-files=no")
    if proc.returncode:
        raise MergeError(proc.stderr.strip() or "git status failed", 4)
    tokens = proc.stdout.split("\0")
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        if not entry:
            continue
        code, changed = entry[:2], entry[3:]
        paths = [changed]
        if ("R" in code or "C" in code) and i < len(tokens):
            paths.append(tokens[i])
            i += 1
        if ignore_beads and all(p.startswith(".beads/") for p in paths):
            continue
        return False
    return True


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


def _worktree_registered(hub: Path, path: Path) -> bool:
    """True when `git worktree list` still lists `path`, whether or not it exists on disk.

    Compared as Unicode NFC: on macOS a directory name holding a non-ASCII
    character can be listed by git in a different normalization form than the
    path helios derives, and a byte-for-byte compare would then miss it (SPEC 12
    step 2, h3c-fix6 item 3).
    """
    listing = _git_output(hub, "worktree", "list", "--porcelain")
    target = unicodedata.normalize("NFC", str(path))
    prefix = "worktree "
    return any(
        unicodedata.normalize("NFC", line[len(prefix):]) == target
        for line in listing.splitlines()
        if line.startswith(prefix)
    )


def _remove_worktree(hub: Path, path: Path, branch: str) -> None:
    """Step 7 cleanup: unlock (even a missing path), remove, prune, then delete the branch.

    Each action is skipped when its target is already gone, so a rerun after a crash
    during step 7 exits 0 (SPEC 12).
    """
    if _worktree_registered(hub, path):
        unlocked = _git(hub, "worktree", "unlock", str(path))
        if unlocked.returncode not in (0, 128):
            raise MergeError(unlocked.stderr.strip() or "worktree unlock failed", 4)
    if path.exists() or path.is_symlink():
        proc = _git(hub, "worktree", "remove", "--force", str(path))
        if proc.returncode:
            raise MergeError(proc.stderr.strip() or "worktree removal failed", 4)
    pruned = _git(hub, "worktree", "prune")
    if pruned.returncode:
        raise MergeError(pruned.stderr.strip() or "worktree prune failed", 4)
    if _git(hub, "show-ref", "--verify", f"refs/heads/{branch}").returncode == 0:
        proc = _git(hub, "branch", "-d", branch)
        if proc.returncode:
            raise MergeError(proc.stderr.strip() or "branch removal failed", 4)


def _git_show_diff_bytes(cwd: Path, commit: str) -> bytes:
    """The raw bytes of `commit`'s diff, immune to ambient diff config (SPEC 12 step 2).

    Every flag that a repo or global config could otherwise use to reshape or hide
    part of the diff is pinned explicitly: submodule pointers always show short
    (never `log`, which git config can otherwise turn into un-parseable multi-line
    text, and never hidden by `ignoreSubmodules`), renames are never folded into a
    single rename entry, and there is no order file, textconv or external diff.
    """
    proc = subprocess.run(
        [
            "git",
            "show",
            "--binary",
            "--no-textconv",
            "--no-ext-diff",
            "--no-color",
            "--no-relative",
            "--no-renames",
            "--no-show-signature",
            "--submodule=short",
            "--ignore-submodules=none",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            "-O/dev/null",
            "--format=",
            commit,
        ],
        cwd=cwd,
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        raise MergeError(proc.stderr.decode("utf-8", "surrogateescape").strip() or f"git show {commit} failed", 4)
    return proc.stdout


def _commit_fingerprints(cwd: Path, base: str, tip: str) -> list[str] | None:
    """A SHA-256 fingerprint of each non-merge commit in `base..tip`, oldest first (SPEC 12 step 2).

    Computed in Python from the raw bytes of `git show`, never from `git patch-id`:
    patch-id can be fooled by a submodule config that reshapes its output, a NUL
    byte, or a mode change moved to another path.
    `index ` lines (blob hashes, which change even for a pure rebase) are dropped;
    `@@ ` hunk headers (line numbers, which shift when an earlier part of the file
    changes) are collapsed to a bare `@@`, so a clean rebase fingerprints the same.
    `None` when a merge commit sits in the range: a verified commit or its rebase
    never contains one (SPEC 12 item 8).
    """
    if _git_output(cwd, "rev-list", "--min-parents=2", f"{base}..{tip}"):
        return None
    commits = _git_output(cwd, "rev-list", "--reverse", f"{base}..{tip}")
    fingerprints = []
    for commit in commits.splitlines() if commits else []:
        raw = _git_show_diff_bytes(cwd, commit)
        kept = []
        for line in raw.split(b"\n"):
            if line.startswith(b"index "):
                continue
            if line.startswith(b"@@ "):
                kept.append(b"@@")
                continue
            kept.append(line)
        fingerprints.append(hashlib.sha256(b"\n".join(kept)).hexdigest())
    return fingerprints


def _is_verified_commit(hub: Path, head: str, output_commit: str) -> bool:
    """True when `head` is `output_commit` rebased: the same non-merge commits, in
    order, by fingerprint, since diverging from main (SPEC 12 step 2).

    An empty verified range (`output_commit` already on main) never passes here:
    equality with `output_commit` is the only acceptance route in that case.
    """
    output_base = _git_output(hub, "merge-base", output_commit, "main")
    output_fingerprints = _commit_fingerprints(hub, output_base, output_commit)
    if not output_fingerprints:
        return False
    head_base = _git_output(hub, "merge-base", head, "main")
    head_fingerprints = _commit_fingerprints(hub, head_base, head)
    return head_fingerprints == output_fingerprints


def _finish_step7(
    hub: Path,
    worktree: Path,
    branch: str,
    beads: BeadsLike,
    bead_id: str,
    marker: str,
) -> None:
    if "origin" in _git_output(hub, "remote").splitlines():
        pushed = _git(hub, "push", "origin", "main")
        if pushed.returncode:
            raise MergeError(pushed.stderr.strip() or "push failed", 4)
        _comment(beads, bead_id, marker + "pushed]", "pushed")
    _remove_worktree(hub, worktree, branch)
    _comment(beads, bead_id, marker + "removed]", "removed")


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
    """Integrate one closed implementation bead, returning ``(exit_code, message)``.

    Steps run in the order 1, 2, 8, 3, 4, 5, 6, 7 (SPEC 12).
    """
    bead = beads.show(bead_id)
    runs_dir = hub / project.runs / bead_id
    lock_path = runs_dir / "lock"
    lock: TextIO | None = None
    if not dry_run:
        runs_dir.mkdir(parents=True, exist_ok=True)
        lock = lock_path.open("a+")
    elif lock_path.exists():
        lock = lock_path.open("r+")
    if lock is not None:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            lock.close()
            raise MergeError("merge lock is held", 2) from exc
    try:
        # Step 1: evidence.
        _verify_evidence(hub, project.runs, bead, beads, input_hashes)

        branch = f"worktree-{bead_id}"
        worktree = (hub / project.worktrees / bead_id).resolve()

        # Step 2: hub on main, both trees clean (the worktree only when it exists).
        branch_check = _git(hub, "symbolic-ref", "--short", "HEAD")
        if branch_check.returncode or branch_check.stdout.strip() != "main":
            raise MergeError("hub is not on main")
        if not _clean(hub, ignore_beads=True):
            raise MergeError("hub or worktree is dirty")
        worktree_exists = worktree.is_dir()
        if worktree_exists and not _clean(worktree, ignore_beads=False):
            raise MergeError("hub or worktree is dirty")

        # Step 8: recovery, only when a completed merge is on main and the worktree
        # holds nothing beyond it (missing, or its HEAD is an ancestor of merge_commit).
        merge_commit = bead.metadata.get("merge_commit")
        merge_commit_completed = bool(merge_commit) and _git(
            hub, "merge-base", "--is-ancestor", str(merge_commit), "main"
        ).returncode == 0
        recovering = False
        if merge_commit_completed:
            if not worktree_exists:
                recovering = True
            else:
                wt_head = _git_output(worktree, "rev-parse", "HEAD")
                recovering = _git(hub, "merge-base", "--is-ancestor", wt_head, str(merge_commit)).returncode == 0

        if recovering:
            main_before = str(bead.metadata.get("merge_main_before", _git_output(hub, "rev-parse", "main")))
            marker = f"[{bead_id}@{main_before}:"
            if dry_run:
                return 0, "would recover and remove worktree"
            _comment(beads, bead_id, marker + "merged]", "merged")
            _finish_step7(hub, worktree, branch, beads, bead_id, marker)
            return 0, "recovered"

        if not worktree_exists:
            raise MergeError(f"worktree missing for {bead_id}")

        # Only a verified commit merges: the worktree must sit on its own branch,
        # at exactly the commit the verify evidence covers, or that commit rebased
        # (same non-merge commits, in order, by fingerprint). Runs on every attempt.
        current_branch = _git(worktree, "symbolic-ref", "--short", "HEAD")
        if current_branch.returncode or current_branch.stdout.strip() != branch:
            raise MergeError(f"worktree is not on branch {branch}")
        head = _git_output(worktree, "rev-parse", "HEAD")
        output_commit = str(bead.metadata.get("output_commit"))
        if _git(hub, "cat-file", "-e", f"{output_commit}^{{commit}}").returncode != 0:
            raise MergeError(f"verified output_commit {output_commit} is not a commit")
        if head != output_commit and not _is_verified_commit(hub, head, output_commit):
            raise MergeError(f"worktree HEAD {head} is not the verified output_commit {output_commit}")
        # Diff against the merge base, falling back to git's well-known empty tree
        # hash when the branch shares no history with main (an orphan branch), so
        # this still lists every path the branch introduces. --no-renames so a
        # rename out of .beads/ still lists its old, .beads/ path. -z (NUL
        # separated, unquoted) so a path holding a non-ASCII byte, a double quote
        # or a tab is not hidden behind git's default quoting (SPEC 12 step 2).
        merge_base = _git(hub, "merge-base", "main", branch)
        base_ref = merge_base.stdout.strip() if merge_base.returncode == 0 else "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
        diff_proc = _git(hub, "diff", "-z", "--no-renames", "--name-only", base_ref, branch)
        if diff_proc.returncode:
            raise MergeError(diff_proc.stderr.strip() or "git diff failed", 4)
        diff_paths = diff_proc.stdout.split("\0")
        if diff_paths and diff_paths[-1] == "":
            diff_paths = diff_paths[:-1]
        if any(p == ".beads" or p.startswith(".beads/") for p in diff_paths):
            raise MergeError("branch changes .beads/")

        # Step 3: record main_before, then rebase.
        main_before = _git_output(hub, "rev-parse", "main")
        marker = f"[{bead_id}@{main_before}:"
        if dry_run:
            return 0, "would rebase, test, merge, push, and remove"
        beads.set_metadata(bead_id, {"merge_main_before": main_before})
        rebase = _git(worktree, "rebase", "main")
        if rebase.returncode:
            rebase_merge = _git_output(worktree, "rev-parse", "--git-path", "rebase-merge")
            rebase_apply = _git_output(worktree, "rev-parse", "--git-path", "rebase-apply")
            in_progress = (worktree / rebase_merge).exists() or (worktree / rebase_apply).exists()
            if in_progress:
                _git(worktree, "rebase", "--abort")
                beads.set_state(bead_id, "run", "conflict", "rebase conflict")
                _comment(beads, bead_id, marker + "conflict]", "conflict")
                raise MergeError("rebase conflict", 3)
            detail = "\n".join(["rebase failed:", *rebase.stderr.strip().splitlines()])
            raise MergeError(detail, 4)
        _comment(beads, bead_id, marker + "rebased]", "rebased")

        # Step 4: checks.
        for name, command in (("test", project.test), ("typecheck", project.typecheck)):
            if not command:
                continue
            exit_code = check_runner(command, worktree)
            if exit_code != 0:
                _comment(beads, bead_id, marker + "test-failed]", "test-failed")
                output = getattr(check_runner, "last_output", "")
                tail = output.splitlines()[-50:]
                raise MergeError("\n".join([f"{name} failed with exit {exit_code}", *tail]), 5)
        _comment(beads, bead_id, marker + "tested]", "tested")

        # Step 5: main must not have moved during the checks.
        if _git_output(hub, "rev-parse", "main") != main_before:
            _comment(beads, bead_id, marker + "main-moved]", "main-moved")
            raise MergeError("main moved during checks", 3)

        # Step 6: record merge_commit before the fast-forward merge.
        commit = _git_output(worktree, "rev-parse", "HEAD")
        beads.set_metadata(bead_id, {"merge_commit": commit})
        merged = _git(hub, "merge", "--ff-only", branch)
        if merged.returncode:
            if _git_output(hub, "rev-parse", "main") != main_before:
                beads.set_metadata(bead_id, {"merge_commit": ""})
                _comment(beads, bead_id, marker + "main-moved]", "main-moved")
                raise MergeError("main moved during merge", 3)
            raise MergeError(merged.stderr.strip() or "fast-forward merge failed", 4)
        _comment(beads, bead_id, marker + "merged]", "merged")

        # Step 7: push (if origin exists) and remove the worktree.
        _finish_step7(hub, worktree, branch, beads, bead_id, marker)
        return 0, "merged"
    finally:
        if lock is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
