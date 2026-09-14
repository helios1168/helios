"""Merge command tests (SPEC section 12)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from helios.beads import Bead, FakeBeads
from helios.config import ProjectConfig
from helios.envelope import AgentReport, Envelope, ExecutionStatus, WorkStatus
from helios.merge import MergeError, merge_bead


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)
    return proc.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    hub = tmp_path / "repo"
    hub.mkdir()
    _git(hub, "init", "-b", "main")
    _git(hub, "config", "user.email", "test@example.com")
    _git(hub, "config", "user.name", "Test")
    (hub / "value.txt").write_text("base\n")
    _git(hub, "add", ".")
    _git(hub, "commit", "-m", "base")
    worktree = tmp_path / "worktree" / "b1"
    worktree.parent.mkdir()
    _git(hub, "worktree", "add", "-b", "worktree-b1", str(worktree), "main")
    (worktree / "value.txt").write_text("merged\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "change")
    return hub, worktree


def _beads(hub: Path, worktree: Path) -> FakeBeads:
    impl = Bead(
        id="b1",
        kind="impl",
        unit="u1",
        status="closed",
        metadata={"output_commit": _git(worktree, "rev-parse", "HEAD")},
    )
    verify = Bead(
        id="v1",
        kind="verify-code",
        unit="u1",
        parent="b1",
        status="closed",
        metadata={"verdict": "verified", "attempt": 1},
        labels=["unit:u1"],
    )
    envelope_dir = hub / ".helios" / "runs" / "v1" / "attempt-1"
    envelope_dir.mkdir(parents=True)
    (envelope_dir / "envelope.json").write_text(
        Envelope(
            task_id="v1",
            attempt=1,
            attempt_id="v1#1",
            kind="verify-code",
            harness="fake",
            started_at="now",
            base_commit=impl.metadata["output_commit"],
            input_hashes={},
            execution_status=ExecutionStatus.COMPLETED,
            report=AgentReport(status=WorkStatus.DONE, summary="ok"),
        ).model_dump_json()
    )
    return FakeBeads([impl, verify])


def test_refuses_missing_envelope_without_bead_writes(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = FakeBeads(
        [
            Bead(id="b1", unit="u1", status="closed", metadata={"output_commit": "x"}),
            Bead(
                id="v1",
                kind="verify-code",
                unit="u1",
                parent="b1",
                status="closed",
                metadata={"verdict": "verified", "attempt": 1},
            ),
        ]
    )
    with pytest.raises(MergeError) as error:
        merge_bead(hub, "b1", project=ProjectConfig(worktrees="../worktree"), beads=beads)
    assert error.value.code == 2
    assert beads.argv_log == []


def test_dry_run_does_not_write_beads(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    assert merge_bead(
        hub,
        "b1",
        project=ProjectConfig(worktrees="../worktree"),
        beads=beads,
        dry_run=True,
    ) == (0, "would rebase, test, merge, push, and remove")
    assert beads.argv_log == []


def test_happy_path_merges_and_removes_worktree(tmp_path: Path) -> None:
    hub, worktree = _repo(tmp_path)
    beads = _beads(hub, worktree)
    assert _git(hub, "status", "--porcelain", "--untracked-files=no") == ""
    assert _git(worktree, "status", "--porcelain", "--untracked-files=no") == ""
    code, message = merge_bead(
        hub,
        "b1",
        project=ProjectConfig(worktrees="../worktree"),
        beads=beads,
        check_runner=lambda _command, _cwd: 0,
    )
    assert (code, message) == (0, "merged")
    assert not worktree.exists()
    assert _git(hub, "show", "main:value.txt") == "merged"
    assert beads.beads["b1"].metadata["merge_commit"] == _git(hub, "rev-parse", "HEAD")
