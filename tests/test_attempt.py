"""Attempt and event tests (SPEC §8, §9.3)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
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


def test_allocate_retries_when_n_is_taken(tmp_path: Path) -> None:
    hub = make_repo(tmp_path / "hub")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    taken = hub / ".helios" / "runs" / "b1" / "attempt-1"
    taken.mkdir(parents=True)
    attempt, _ = att.allocate(hub=hub, runs_rel=".helios/runs", bead="b1", worktree=worktree)
    assert attempt.n == 2


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


def test_pid_start_defaults_null_and_survives_transitions(tmp_path: Path) -> None:
    d = tmp_path / "b" / "attempt-1"
    att.write_state(d, attempt_id="b#1", state="allocated")
    assert att.read_state(d)["pid_start"] is None
    att.transition(d, "launched", pid=4242, pid_start="Sun Sep 14 00:00:00 2026")
    assert att.read_state(d)["pid_start"] == "Sun Sep 14 00:00:00 2026"
    # A later transition that does not set pid_start keeps the stored value.
    att.transition(d, "native_completed")
    assert att.read_state(d)["pid_start"] == "Sun Sep 14 00:00:00 2026"


def test_legacy_state_json_without_pid_start_reads_as_null(tmp_path: Path) -> None:
    d = tmp_path / "b" / "attempt-1"
    d.mkdir(parents=True)
    (d / "state.json").write_text(json.dumps({
        "state": "launched", "attempt_id": "b#1", "pid": 4242,
        "session_id": "s1", "execution_status": None, "updated": att.utc_now(),
    }))
    state = att.read_state(d)
    assert state.get("pid_start") is None
    # A transition on a pre-upgrade file still works and now carries all seven keys.
    record = att.transition(d, "finalized")
    assert set(record) == {"state", "attempt_id", "pid", "pid_start",
                            "session_id", "execution_status", "updated"}
    assert record["pid_start"] is None


def test_is_pid_alive_null_pid(tmp_path: Path) -> None:
    assert att.is_pid_alive(None) is False
    assert att.is_pid_alive(None, "whatever") is False


def test_is_pid_alive_null_pid_start_falls_back_to_group_test() -> None:
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        assert att.is_pid_alive(proc.pid) is True
        assert att.is_pid_alive(proc.pid, None) is True
    finally:
        proc.kill()
        proc.wait()
    assert att.is_pid_alive(proc.pid, None) is False


def test_is_pid_alive_reused_pid_start_mismatch_is_not_live(monkeypatch: pytest.MonkeyPatch) -> None:
    # The leader (this test process) exists, but its recorded start time no
    # longer matches: a reused pid, never live.
    monkeypatch.setattr(att, "read_pid_start", lambda pid: "some-other-start-time")
    assert att.is_pid_alive(os.getpid(), "original-start-time") is False


def test_is_pid_alive_leader_reaped_falls_back_to_group_test(tmp_path: Path) -> None:
    # The bash leader starts a grandchild in the same process group, then
    # exits; the leader pid is reaped, but the group (via the child) lives on.
    child_pid_file = tmp_path / "child.pid"
    leader = subprocess.Popen(
        ["bash", "-c", f"sleep 30 & echo $! > {child_pid_file}"],
        start_new_session=True,
    )
    leader.wait(timeout=5)
    child_pid = int(child_pid_file.read_text().strip())
    try:
        with pytest.raises(ProcessLookupError):
            os.kill(leader.pid, 0)
        assert att.is_pid_alive(leader.pid, "a-start-time-that-does-not-matter") is True
    finally:
        try:
            os.killpg(leader.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        for _ in range(50):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)


def test_read_pid_start_ignores_caller_locale_and_timezone() -> None:
    """read_pid_start forces a fixed C locale and UTC time zone for its own
    `ps` call, so the text never depends on the calling process's own
    ambient LC_ALL/LANG/TZ (SPEC §7.1 step 7, rule item 1)."""
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        script = f"from helios import attempt as att; print(att.read_pid_start({proc.pid}) or '')"
        foreign_env = dict(os.environ, LC_ALL="de_DE.UTF-8", LANG="de_DE.UTF-8", TZ="Asia/Tokyo")
        c_env = {k: v for k, v in os.environ.items() if k not in ("LC_ALL", "LANG", "LC_TIME", "TZ")}
        c_env.update(LC_ALL="C", LANG="C", TZ="UTC")
        written = subprocess.run([sys.executable, "-c", script], env=foreign_env,
                                 capture_output=True, text=True, check=True).stdout.strip()
        compared = subprocess.run([sys.executable, "-c", script], env=c_env,
                                  capture_output=True, text=True, check=True).stdout.strip()
        assert written and written == compared
        assert att.is_pid_alive(proc.pid, written) is True
    finally:
        proc.kill()
        proc.wait()


def test_read_pid_start_passes_timeout_and_returns_null_on_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_timeout = []

    def fake_run(*args, **kwargs):
        seen_timeout.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(cmd=args[0] if args else "ps", timeout=kwargs.get("timeout") or 5)

    monkeypatch.setattr(att.subprocess, "run", fake_run)
    assert att.read_pid_start(12345) is None
    assert seen_timeout == [5]


def test_is_pid_alive_falls_through_when_ps_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """A live leader whose `ps` read times out (rule item 3) falls back to
    the process-group test instead of reading dead."""
    monkeypatch.setattr(att, "read_pid_start", lambda pid: None)
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        assert att.is_pid_alive(proc.pid, "some-recorded-start-time") is True
    finally:
        proc.kill()
        proc.wait()


def test_is_pid_alive_falls_through_when_leader_vanishes_before_ps_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The leader exists at the first check but is gone by the time `ps`
    runs (a race): `ps` reports nothing (null), so fall through to the
    group test rather than reading dead (rule item 3)."""
    child_pid_file = tmp_path / "child.pid"
    leader_script = (
        "import subprocess, sys\n"
        "child = subprocess.Popen(['sleep', '30'])\n"
        "open(sys.argv[1], 'w').write(str(child.pid))\n"
    )
    leader = subprocess.Popen(
        [sys.executable, "-c", leader_script, str(child_pid_file)], start_new_session=True
    )
    leader.wait(timeout=5)
    child_pid = int(child_pid_file.read_text().strip())
    monkeypatch.setattr(att, "_leader_exists", lambda pid: True)
    monkeypatch.setattr(att, "read_pid_start", lambda pid: None)
    try:
        assert att.is_pid_alive(leader.pid, "a-start-time-that-no-longer-matters") is True
    finally:
        try:
            os.killpg(leader.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        for _ in range(50):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)


def test_is_pid_alive_ps_missing_from_path_reads_not_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ps` entirely missing from PATH is not covered by the item-3
    fallback (a documented follow-up): the attempt reads as not live even
    though the process group is genuinely alive."""
    monkeypatch.setattr(att.shutil, "which", lambda name: None)
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        assert att.is_pid_alive(proc.pid, "whatever-start-time") is False
    finally:
        proc.kill()
        proc.wait()
