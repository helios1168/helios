"""Attempt and event tests (SPEC §8, §9.3)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from helios import attempt as att
from helios import events


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    return path


def test_allocation_numbers_moves_stale_writes_atomically(tmp_path: Path) -> None:
    hub = make_repo(tmp_path / "hub")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    first, _ = att.allocate(hub=hub, runs_rel=".helios/runs", bead="b1", worktree=worktree)
    assert first.n == 1 and first.attempt_id == "b1#1"
    # A leftover report at the next attempt path is stale: moved aside.
    stale = att.worktree_report_path(worktree, 2)
    stale.parent.mkdir(parents=True)
    stale.write_text('{"status": "done"}')
    second, notes = att.allocate(hub=hub, runs_rel=".helios/runs", bead="b1", worktree=worktree)
    assert second.n == 2
    assert (second.dir / "stale-report.json").read_text() == '{"status": "done"}'
    assert not stale.exists() and notes
    state = att.read_state(second.dir)
    assert state["state"] == "allocated"
    assert state["attempt_id"] == "b1#2"
    att.transition(second.dir, "launched", pid=4242, session_id="s1")
    lines = (second.dir / "state.log").read_text().splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["state"] for line in lines] == ["allocated", "launched"]


def test_recovery_classification() -> None:
    assert att.classify_recovery(None, pid_alive=False) == "new"
    assert att.classify_recovery("finalized", pid_alive=False) == "new"
    assert att.classify_recovery("launched", pid_alive=True) == "refuse"
    assert att.classify_recovery("native_completed", pid_alive=False) == "resume"
    assert att.classify_recovery("validated", pid_alive=False) == "resume"
    assert att.classify_recovery("invalid", pid_alive=False) == "resume"
    assert att.classify_recovery("launched", pid_alive=False) == "crash_and_new"
    assert att.classify_recovery("allocated", pid_alive=False) == "crash_and_new"
    assert att.classify_recovery("interrupted", pid_alive=False) == "finalize_and_new"
    assert att.classify_recovery("timed_out", pid_alive=False) == "finalize_and_new"
    assert att.classify_recovery("crashed", pid_alive=False) == "finalize_and_new"
    assert att.classify_recovery("launch_failed", pid_alive=False) == "finalize_and_new"
    assert att.classify_recovery("interrupted", pid_alive=True) == "refuse"


def test_is_stale_on_hashes_and_ancestry(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / "f").write_text("a")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "one"], cwd=repo, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo / "f").write_text("b")
    subprocess.run(["git", "commit", "-qam", "two"], cwd=repo, check=True)
    tip = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    env = {"input_hashes": {"prompt": "p"}, "base_commit": base}
    assert not att.is_stale(env, {"prompt": "p"}, repo=repo, impl_commit=tip)
    assert att.is_stale(env, {"prompt": "q"}, repo=repo, impl_commit=tip)
    assert att.is_stale(
        {"input_hashes": {"prompt": "p"}, "base_commit": tip},
        {"prompt": "p"},
        repo=repo,
        impl_commit=base,
    )


def test_events_append_writes_spec_fields(tmp_path: Path) -> None:
    events.append(
        tmp_path, source="helios", type="launched", bead="b1",
        attempt="b1#1", session="codex:s1", detail="go",
    )
    events.append(tmp_path, source="orchestrator", type="finalized", bead="b1")
    lines = (tmp_path / ".helios" / "events.jsonl").read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["source"] == "helios" and first["type"] == "launched"
    assert first["bead"] == "b1" and first["attempt"] == "b1#1"
    assert first["session"] == "codex:s1" and first["detail"] == "go"
    assert set(first) == {"ts", "source", "type", "bead", "attempt", "session", "detail"}
    assert all(len(line.encode()) <= 4096 for line in lines)


def test_events_line_is_at_most_4096_bytes(tmp_path: Path) -> None:
    events.append(tmp_path, source="helios", type="launched", bead="b1", detail="x" * 4090)
    raw = (tmp_path / ".helios" / "events.jsonl").read_bytes()
    assert len(raw) <= 4096
    with pytest.raises(ValueError):
        events.append(tmp_path, source="helios", type="launched", bead="b" * 5000)
