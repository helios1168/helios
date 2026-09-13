"""Ownership and worktree tests (SPEC §7.3, §7.4)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from helios import ownership, worktree


def make_repo(path: Path, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
    return path


def head(path: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_ownership_sorts_allowed_and_rejected(tmp_path: Path) -> None:
    hub = make_repo(tmp_path / "hub")
    base = head(hub)
    (hub / "src").mkdir()
    (hub / "src" / "a.py").write_text("x\n")
    (hub / "tests").mkdir()
    (hub / "tests" / "t.py").write_text("y\n")
    (hub / ".helios").mkdir()
    (hub / ".helios" / "events.jsonl").write_text("{}\n")
    (hub / "secret.txt").write_text("s\n")
    result = ownership.check(
        worktree=hub,
        base_commit=base,
        files=["src/"],
        confidential=("secret*.txt",),
    )
    assert "src/a.py" in result.allowed
    assert "tests/t.py" in result.allowed
    assert ".helios/events.jsonl" in result.rejected
    assert "secret.txt" in result.rejected
    assert not result.passed
    assert "secret.txt" in result.detail


def test_ownership_verify_kinds_are_scoped(tmp_path: Path) -> None:
    hub = make_repo(tmp_path / "hub")
    base = head(hub)
    (hub / "tools" / "verify" / "U1").mkdir(parents=True)
    (hub / "tools" / "verify" / "U1" / "check.py").write_text("x\n")
    (hub / "src").mkdir()
    (hub / "src" / "a.py").write_text("x\n")
    result = ownership.check(
        worktree=hub, base_commit=base, files=["src/"], kind="verify-code", unit="U1"
    )
    assert result.allowed == ("tools/verify/U1/check.py",)
    assert result.rejected == ("src/a.py",)


def test_ownership_rename_checks_both_sides(tmp_path: Path) -> None:
    hub = make_repo(tmp_path / "hub")
    base = head(hub)
    (hub / "tests").mkdir()
    subprocess.run(["git", "mv", "README.md", "tests/README.md"], cwd=hub, check=True)
    result = ownership.check(worktree=hub, base_commit=base, files=["tests/"])
    assert "tests/README.md" in result.allowed
    assert "README.md" in result.rejected


def test_ownership_non_ascii_and_strict_star(tmp_path: Path) -> None:
    hub = make_repo(tmp_path / "hub")
    base = head(hub)
    (hub / "täst.txt").write_text("u\n")
    (hub / "src").mkdir()
    (hub / "src" / "a.py").write_text("x\n")
    (hub / "src" / "sub").mkdir()
    (hub / "src" / "sub" / "x.py").write_text("x\n")
    result = ownership.check(
        worktree=hub, base_commit=base, files=["täst.txt", "src/*.py"]
    )
    assert "täst.txt" in result.allowed
    assert "src/a.py" in result.allowed
    assert "src/sub/x.py" in result.rejected


def test_worktree_refuses_plain_directory(tmp_path: Path) -> None:
    hub = make_repo(tmp_path / "hub")
    plain = hub / ".claude" / "worktrees" / "b9"
    plain.mkdir(parents=True)
    (plain / "note.txt").write_text("not a worktree\n")
    hub_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=hub, check=True, capture_output=True, text=True,
    ).stdout.strip()
    with pytest.raises(worktree.WorktreeError, match="is not a worktree"):
        worktree.prepare(hub=hub, bead="b9")
    try:
        worktree.prepare(hub=hub, bead="b9")
    except worktree.WorktreeError as exc:
        assert hub_branch in str(exc)


def test_worktree_prepare_creates_reuses_and_refuses(tmp_path: Path) -> None:
    hub = make_repo(tmp_path / "hub")
    (hub / ".env").write_text("KEY=1\n")
    info = worktree.prepare(hub=hub, bead="b1", link_into_worktrees=(".env", "missing/*"))
    assert info.branch == "worktree-b1"
    assert info.created
    assert info.base_commit == head(info.path)
    assert (info.path / ".env").is_symlink()
    locked = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=hub, check=True, capture_output=True, text=True,
    ).stdout
    assert "locked" in locked
    again = worktree.prepare(hub=hub, bead="b1")
    assert not again.created and again.path == info.path
    (info.path / "stray.txt").write_text("s\n")
    worktree.prepare(hub=hub, bead="b1", again=True)
    assert not (info.path / "stray.txt").exists()
    other = hub / ".claude" / "worktrees" / "b2"
    other.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", str(other), "-b", "something-else", "main"],
        cwd=hub, check=True, capture_output=True,
    )
    with pytest.raises(worktree.WorktreeError, match="something-else"):
        worktree.prepare(hub=hub, bead="b2")
