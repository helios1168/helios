"""Run pipeline tests with the fake harness (SPEC §6.3, §7.1, §8).

Each test drives ``helios.run`` against a temporary git repository with
``FakeBeads``. Outcomes: valid done, invalid output, missing output,
timeout, interrupt, stale report, ownership failure, needs_input,
recovery without a new attempt, idempotent write-back, dry-run, parallel.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from helios import attempt as attempt_mod
from helios import beads as beads_mod
from helios import config as config_mod
from helios import envelope as envelope_mod
from helios import run as run_mod
from helios import worktree as worktree_mod
from helios.harness import get as harness_get
from helios.harness.base import LaunchSpec


def make_hub(tmp_path: Path) -> Path:
    hub = tmp_path / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=hub, check=True)
    (hub / "README.md").write_text("hi\n")
    (hub / ".gitignore").write_text(".helios/\n.claude/worktrees/\n")
    (hub / "skills" / "impl").mkdir(parents=True)
    (hub / "skills" / "impl" / "SKILL.md").write_text(
        "---\nname: impl\n---\n\n# Implementation bead\n\nDo the work.\n"
    )
    (hub / "AGENTS.md").write_text(
        "# helios\n\n## Worker contract\n\nWork exactly one bead.\n\n## Orchestrator\n\nMerges.\n"
    )
    subprocess.run(["git", "add", "."], cwd=hub, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=hub, check=True)
    return hub


def make_bead(bead_id: str = "b1", **kw) -> beads_mod.Bead:
    base = dict(id=bead_id, kind="impl", files=["src/"], test="true", docs=[], memories=[])
    base.update(kw)
    return beads_mod.Bead(**base)


def write_script(tmp_path: Path, payload: dict, name: str = "script.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    return path


def set_fake(monkeypatch: pytest.MonkeyPatch, script: Path) -> None:
    monkeypatch.setenv("HELIOS_FAKE_SCRIPT", str(script))


def read_envelope(hub: Path, bead: str, n: int) -> dict:
    path = hub / ".helios" / "runs" / bead / f"attempt-{n}" / "envelope.json"
    data = json.loads(path.read_text())
    envelope_mod.Envelope.model_validate(data)
    return data


def test_harness_get_fake() -> None:
    harness = harness_get("fake")
    assert harness.name == "fake"
    spec = LaunchSpec(
        bead="b1",
        attempt=1,
        worktree=Path("/tmp/wt"),
        prompt="p",
        report_path=Path("/tmp/r.json"),
        report_schema_path=Path("/tmp/s.json"),
        raw_dir=Path("/tmp/raw"),
        env={},
    )
    argv = harness.argv(spec)
    assert argv[:3] == [sys.executable, "-m", "helios.harness.fake"]
    assert harness.stdin_text(spec) is None
    assert harness.attach_command("s1", spec)[:3] == argv[:3]
    with pytest.raises(ValueError):
        harness_get("nope")


def test_fake_module_writes_report_and_stdout(tmp_path: Path, capsys) -> None:
    import os

    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 0, "stdout": "hello",
         "session_id": "s9", "report": {"status": "done", "summary": "ok"}},
    )
    report = tmp_path / "report.json"
    os.environ["HELIOS_FAKE_SCRIPT"] = str(script)
    os.environ["HELIOS_REPORT"] = str(report)
    try:
        from helios.harness.fake import run_fake

        assert run_fake() == 0
    finally:
        del os.environ["HELIOS_FAKE_SCRIPT"]
        del os.environ["HELIOS_REPORT"]
    assert json.loads(report.read_text()) == {"status": "done", "summary": "ok"}
    assert "hello" in capsys.readouterr().out


def test_a_valid_done_commits_closes_events(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    bead = make_bead("b1", files=["src/"],
                     test="mkdir -p src && echo hi > src/out.txt && exit 0")
    beads = beads_mod.FakeBeads([bead])
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 0, "stdout": "ok", "session_id": "s1",
         "report": {"status": "done", "summary": "did it"}},
    )
    set_fake(monkeypatch, script)
    cfg = config_mod.load(hub)
    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg,
                         harness_override="fake")
    assert rc == 0
    env = read_envelope(hub, "b1", 1)
    assert env["execution_status"] == "completed"
    assert env["report"]["status"] == "done"
    assert env["output_commit"] is not None
    assert env["attempt_id"] == "b1#1"
    assert env["session_id"] == "s1"
    assert any(c["name"] == "test" and c["passed"] for c in env["checks"])
    assert "b1" in beads.closed
    worktree = hub / ".claude" / "worktrees" / "b1"
    assert (worktree / "src" / "out.txt").read_text() == "hi\n"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=worktree, check=True,
        capture_output=True, text=True).stdout.strip()
    assert env["output_commit"] == head
    events = (hub / ".helios" / "events.jsonl").read_text().splitlines()
    types = [json.loads(line)["type"] for line in events]
    assert "launched" in types and "finalized" in types
    state = attempt_mod.read_state(hub / ".helios" / "runs" / "b1" / "attempt-1")
    assert state["state"] == "finalized"


def test_b_invalid_output(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 0, "stdout": "x", "session_id": "s1",
         "report": None, "report_text": "not json{"},
    )
    set_fake(monkeypatch, script)
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    assert rc == 4
    env = read_envelope(hub, "b1", 1)
    assert env["execution_status"] == "invalid_output"
    assert env["report"] is None and env["report_error"]
    assert "b1" not in beads.closed


def test_b_invalid_schema_report(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 0, "stdout": "x", "session_id": "s1",
         "report": {"status": "done"}},
    )
    set_fake(monkeypatch, script)
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    assert rc == 4
    assert read_envelope(hub, "b1", 1)["execution_status"] == "invalid_output"


def test_c_missing_output(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 0, "stdout": "x", "session_id": "s1",
         "report": None, "report_text": None},
    )
    set_fake(monkeypatch, script)
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    assert rc == 4
    assert read_envelope(hub, "b1", 1)["execution_status"] == "missing_output"


def test_d_timeout_kills_process(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 30, "stdout": "slow", "session_id": "s1",
         "report": {"status": "done", "summary": "late"}},
    )
    set_fake(monkeypatch, script)
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake",
                         timeout_s=1)
    assert rc == 4
    env = read_envelope(hub, "b1", 1)
    assert env["execution_status"] == "timed_out"
    state = attempt_mod.read_state(hub / ".helios" / "runs" / "b1" / "attempt-1")
    assert not attempt_mod.is_pid_alive(state.get("pid"))


CHILD_RUNNER = """\
import os, sys
from pathlib import Path
from helios import beads as B, config as C, run as R
hub = Path(os.environ["HELIOS_CHILD_HUB"])
ids = os.environ["HELIOS_CHILD_BEADS"].split(",")
beads = B.FakeBeads(
    [B.Bead(id=i, kind="impl", files=[f"src/{i}/"], test="true") for i in ids]
)
rc = R.run_many(ids, hub=hub, beads=beads, config=C.load(hub),
                harness_override="fake", max_parallel=int(os.environ["HELIOS_CHILD_MAXP"]))
sys.exit(rc)
"""


def spawn_child(hub: Path, script: Path, bead_ids: str, maxp: int = 1):
    """Run helios in a real subprocess so a real SIGINT can be delivered."""
    env = dict(os.environ, HELIOS_FAKE_SCRIPT=str(script),
               HELIOS_CHILD_HUB=str(hub), HELIOS_CHILD_BEADS=bead_ids,
               HELIOS_CHILD_MAXP=str(maxp))
    return subprocess.Popen(
        [sys.executable, "-c", CHILD_RUNNER],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )


def wait_for(path: Path, needle: str = "", limit: float = 20) -> None:
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if path.exists() and needle in path.read_text():
            return
        time.sleep(0.05)
    raise TimeoutError(str(path))


def test_e_real_sigint_records_interrupted(tmp_path: Path) -> None:
    hub = make_hub(tmp_path)
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 30, "stdout": "slow", "session_id": "s1",
         "report": {"status": "done", "summary": "late"}},
    )
    proc = spawn_child(hub, script, "b1")
    wait_for(hub / ".helios" / "runs" / "b1" / "attempt-1" / "stdout.jsonl", "slow")
    os.kill(proc.pid, signal.SIGINT)
    proc.communicate(timeout=60)
    assert proc.returncode == 4
    env = read_envelope(hub, "b1", 1)
    assert env["execution_status"] == "interrupted"
    state = attempt_mod.read_state(hub / ".helios" / "runs" / "b1" / "attempt-1")
    assert not attempt_mod.is_pid_alive(state.get("pid"))


def test_e_real_sigint_parallel(tmp_path: Path) -> None:
    hub = make_hub(tmp_path)
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 30, "stdout": "slow", "session_id": "s1",
         "report": {"status": "done", "summary": "late"}},
    )
    proc = spawn_child(hub, script, "b1,b2", maxp=2)
    for bid in ("b1", "b2"):
        wait_for(hub / ".helios" / "runs" / bid / "attempt-1" / "stdout.jsonl", "slow")
    os.kill(proc.pid, signal.SIGINT)
    proc.communicate(timeout=60)
    assert proc.returncode == 4
    assert read_envelope(hub, "b1", 1)["execution_status"] == "interrupted"
    assert read_envelope(hub, "b2", 1)["execution_status"] == "interrupted"


def test_f_stale_report_not_accepted(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    info = worktree_mod.prepare(hub=hub, bead="b1")
    stale = attempt_mod.worktree_report_path(info.path, 1)
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(json.dumps({"status": "done", "summary": "old"}))
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 0, "stdout": "new", "session_id": "s1",
         "report": {"status": "done", "summary": "new"}},
    )
    set_fake(monkeypatch, script)
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    assert rc == 0
    attempt_dir = hub / ".helios" / "runs" / "b1" / "attempt-1"
    assert json.loads((attempt_dir / "stale-report.json").read_text())["summary"] == "old"
    env = read_envelope(hub, "b1", 1)
    assert env["report"]["summary"] == "new"
    assert any("stale" in note for note in env["notes"])


def test_g_ownership_failure_commits_nothing(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    bead = make_bead("b1", files=["src/allowed/"],
                     test="mkdir -p src/allowed && echo ok > src/allowed/a.txt"
                          " && echo bad > outside.txt && exit 0")
    beads = beads_mod.FakeBeads([bead])
    base_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=hub, check=True,
        capture_output=True, text=True).stdout.strip()
    _ = base_before
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 0, "stdout": "x", "session_id": "s1",
         "report": {"status": "done", "summary": "did it"}},
    )
    set_fake(monkeypatch, script)
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    assert rc == 5
    env = read_envelope(hub, "b1", 1)
    assert env["execution_status"] == "completed"
    assert env["output_commit"] is None
    assert any(c["name"] == "ownership" and not c["passed"] for c in env["checks"])
    assert "outside.txt" in env["checks"][-1]["detail"]
    worktree = hub / ".claude" / "worktrees" / "b1"
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=worktree, check=True,
        capture_output=True, text=True).stdout.strip().splitlines()
    assert len(log) == 1


def test_h_needs_input_stays_open(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 0, "stdout": "x", "session_id": "s1",
         "report": {"status": "needs_input", "summary": "stuck",
                    "question": "which file?"}},
    )
    set_fake(monkeypatch, script)
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    assert rc == 3
    env = read_envelope(hub, "b1", 1)
    assert env["execution_status"] == "completed"
    assert "b1" not in beads.closed
    texts = [c.text for c in beads.comments("b1")]
    assert any(t.startswith("question: [b1#1]") and "which file?" in t for t in texts)
    assert beads.states["b1"]["run"] == "waiting"


def test_i_native_completed_resumes_without_new_attempt(
    tmp_path: Path, monkeypatch
) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    cfg = config_mod.load(hub)
    info = worktree_mod.prepare(hub=hub, bead="b1")
    attempt_obj, _ = attempt_mod.allocate(
        hub=hub, runs_rel=cfg.project.runs, bead="b1", worktree=info.path)
    assert attempt_obj.n == 1
    report_path = attempt_mod.worktree_report_path(info.path, 1)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({"status": "done", "summary": "first"}))
    (attempt_obj.dir / "stdout.jsonl").write_text("ok\n")
    (attempt_obj.dir / "input.json").write_text(json.dumps(
        {"input_hashes": {}, "base_commit": info.base_commit, "bead": "b1"}))
    attempt_mod.transition(attempt_obj.dir, "launched", pid=None)
    attempt_mod.transition(attempt_obj.dir, "native_completed")
    script = write_script(
        tmp_path, {"session_id": "s-resume", "stdout": "ok"})
    set_fake(monkeypatch, script)
    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg,
                         harness_override="fake")
    assert rc == 0
    numbers = attempt_mod.existing_attempts(hub / ".helios" / "runs" / "b1")
    assert numbers == [1]
    env = read_envelope(hub, "b1", 1)
    assert env["execution_status"] == "completed"
    assert "b1" in beads.closed


def test_j_writeback_twice_adds_no_duplicates(
    tmp_path: Path, monkeypatch
) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 0, "stdout": "x", "session_id": "s1",
         "report": {"status": "done", "summary": "did it",
                    "learned": ["keep"], "followups": ["next"]}},
    )
    set_fake(monkeypatch, script)
    assert run_mod.run_one("b1", hub=hub, beads=beads,
                           config=config_mod.load(hub),
                           harness_override="fake") == 0
    before = list(beads.comments("b1"))
    env = read_envelope(hub, "b1", 1)
    plan = beads_mod.plan_writeback(
        attempt_id="b1#1", bead_kind="impl", harness="fake",
        session_id=env["session_id"], worktree="wt", attempt=1,
        execution_status="completed", verdict=None,
        output_commit=env["output_commit"], report_status="done",
        report_summary="did it", learned=["keep"], missing_context=[],
        followups=["next"], question=None, checks_passed=True,
    )
    assert beads_mod.apply_writeback(beads, "b1", plan) == 0
    assert [c.text for c in beads.comments("b1")] == [c.text for c in before]


def test_k_dry_run_creates_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    script = write_script(tmp_path, {"session_id": "s1"})
    set_fake(monkeypatch, script)
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub),
                         harness_override="fake", dry_run=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "harness: fake" in out and "argv:" in out
    assert "worktree:" in out and "attempt:" in out and "prompt_bytes:" in out
    assert not (hub / ".helios" / "runs").exists()
    assert not (hub / ".claude" / "worktrees").exists()


def test_parallel_beads(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([
        make_bead("b1", files=["src/a/"], test="true"),
        make_bead("b2", files=["src/b/"], test="true"),
    ])
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 0, "stdout": "x", "session_id": "s1",
         "report": {"status": "done", "summary": "ok"}},
    )
    set_fake(monkeypatch, script)
    rc = run_mod.run_many(["b1", "b2"], hub=hub, beads=beads,
                          config=config_mod.load(hub),
                          harness_override="fake", max_parallel=2)
    assert rc == 0
    assert read_envelope(hub, "b1", 1)["execution_status"] == "completed"
    assert read_envelope(hub, "b2", 1)["execution_status"] == "completed"


def test_launched_state_has_live_pid(tmp_path: Path) -> None:
    hub = make_hub(tmp_path)
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 30, "stdout": "slow", "session_id": "s1",
         "report": {"status": "done", "summary": "slow"}},
    )
    proc = spawn_child(hub, script, "b1")
    try:
        a1 = hub / ".helios" / "runs" / "b1" / "attempt-1"
        wait_for(a1 / "stdout.jsonl", "slow")
        mid = attempt_mod.read_state(a1)
        assert mid["state"] == "launched" and mid["pid"]
        assert attempt_mod.is_pid_alive(mid["pid"])
    finally:
        os.kill(proc.pid, signal.SIGINT)
        proc.communicate(timeout=60)


def test_second_run_during_live_attempt_refuses(tmp_path: Path) -> None:
    hub = make_hub(tmp_path)
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 30, "stdout": "slow", "session_id": "s1",
         "report": {"status": "done", "summary": "slow"}},
    )
    proc = spawn_child(hub, script, "b1")
    try:
        a1 = hub / ".helios" / "runs" / "b1" / "attempt-1"
        wait_for(a1 / "stdout.jsonl", "slow")
        beads = beads_mod.FakeBeads(
            [beads_mod.Bead(id="b1", kind="impl", files=["src/b1/"], test="true")])
        rc = run_mod.run_one("b1", hub=hub, beads=beads,
                             config=config_mod.load(hub), harness_override="fake")
        assert rc == 2
        assert not (hub / ".helios" / "runs" / "b1" / "attempt-2").exists()
    finally:
        os.kill(proc.pid, signal.SIGINT)
        proc.communicate(timeout=60)
    assert not (hub / ".helios" / "runs" / "b1" / "attempt-2").exists()


def test_crash_and_new_records_crashed(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    info = worktree_mod.prepare(hub=hub, bead="b1")
    attempt_mod.allocate(hub=hub, runs_rel=cfg.project.runs, bead="b1",
                         worktree=info.path)
    script = write_script(
        tmp_path, {"exit_code": 0, "sleep_s": 0, "stdout": "x", "session_id": "s1",
                   "report": {"status": "done", "summary": "ok"}})
    set_fake(monkeypatch, script)
    beads = beads_mod.FakeBeads(
        [beads_mod.Bead(id="b1", kind="impl", files=["src/b1/"], test="true")])
    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg,
                         harness_override="fake")
    assert rc == 0
    log = (hub / ".helios" / "runs" / "b1" / "attempt-1" / "state.log").read_text()
    order = [json.loads(line)["state"] for line in log.splitlines()]
    assert order.index("crashed") < order.index("finalized")
    assert (hub / ".helios" / "runs" / "b1" / "attempt-2").exists()


def test_dry_run_with_resumable_attempt_creates_nothing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    info = worktree_mod.prepare(hub=hub, bead="b1")
    att, _ = attempt_mod.allocate(
        hub=hub, runs_rel=cfg.project.runs, bead="b1", worktree=info.path)
    rp = attempt_mod.worktree_report_path(info.path, 1)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps({"status": "done", "summary": "ok"}))
    (att.dir / "stdout.jsonl").write_text("ok\n")
    (att.dir / "input.json").write_text(json.dumps(
        {"input_hashes": {}, "base_commit": info.base_commit}))
    attempt_mod.transition(att.dir, "launched")
    attempt_mod.transition(att.dir, "native_completed")
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=info.path, check=True,
        capture_output=True, text=True).stdout.strip()
    set_fake(monkeypatch, write_script(tmp_path, {}))
    beads = beads_mod.FakeBeads(
        [beads_mod.Bead(id="b1", kind="impl", files=["src/b1/"], test="true")])
    rc = run_mod.run_many(["b1"], hub=hub, beads=beads, config=cfg,
                          harness_override="fake", dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0 and "recovery:" in out
    assert attempt_mod.read_state(att.dir)["state"] == "native_completed"
    assert not (att.dir / "envelope.json").exists()
    assert not beads.closed
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=info.path, check=True,
        capture_output=True, text=True).stdout.strip() == head_before
    assert not (hub / ".helios" / "events.jsonl").exists()


def test_dry_run_with_crashed_attempt_creates_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    info = worktree_mod.prepare(hub=hub, bead="b1")
    att, _ = attempt_mod.allocate(
        hub=hub, runs_rel=cfg.project.runs, bead="b1", worktree=info.path)
    attempt_mod.transition(att.dir, "launched")
    attempt_mod.transition(att.dir, "crashed")
    set_fake(monkeypatch, write_script(tmp_path, {}))
    beads = beads_mod.FakeBeads(
        [beads_mod.Bead(id="b1", kind="impl", files=["src/b1/"], test="true")])
    run_mod.run_many(["b1"], hub=hub, beads=beads, config=cfg,
                     harness_override="fake", dry_run=True)
    assert attempt_mod.read_state(att.dir)["state"] == "crashed"


def test_done_with_failing_test_sets_failed_state(
    tmp_path: Path, monkeypatch
) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1", test="false")])
    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "stdout": "x", "session_id": "s1",
                   "report": {"status": "done", "summary": "s"}}))
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    assert rc == 5
    assert beads.states["b1"]["run"] == "failed"
    assert "b1" not in beads.closed


def test_blocked_report_sets_blocked_state(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "stdout": "x", "session_id": "s1",
                   "report": {"status": "blocked", "summary": "s"}}))
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    assert rc == 3
    assert beads.states["b1"]["run"] == "blocked"


def test_missing_skill_is_preflight(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([beads_mod.Bead(
        id="b1", kind="verify-code", unit="U1", parent="b0", files=[])])
    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "stdout": "x",
                   "report": {"status": "done", "summary": "s"}}))
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    assert rc == 2
    assert not (hub / ".helios" / "runs").exists()
    assert not (hub / ".claude" / "worktrees").exists()


def test_output_commit_is_head_when_nothing_to_commit(
    tmp_path: Path, monkeypatch
) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "stdout": "x", "session_id": "s1",
                   "report": {"status": "done", "summary": "s"}}))
    assert run_mod.run_one("b1", hub=hub, beads=beads,
                           config=config_mod.load(hub),
                           harness_override="fake") == 0
    env = read_envelope(hub, "b1", 1)
    assert env["output_commit"] == env["base_commit"]


def test_link_symlink_and_confidential_not_staged(
    tmp_path: Path, monkeypatch
) -> None:
    toml = '[project]\nlink_into_worktrees = ["secret.pem"]\nconfidential = ["*.pem"]\n'
    (tmp_path / "hub").mkdir(parents=True, exist_ok=True)
    hub = tmp_path / "hub"
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=hub, check=True)
    (hub / "README.md").write_text("hi\n")
    (hub / ".gitignore").write_text(".helios/\n.claude/worktrees/\n")
    (hub / "skills" / "impl").mkdir(parents=True)
    (hub / "skills" / "impl" / "SKILL.md").write_text("---\nname: impl\n---\n\nDo.\n")
    (hub / "AGENTS.md").write_text("# h\n\n## Worker contract\n\nOne bead.\n")
    (hub / ".agents").mkdir()
    (hub / ".agents" / "workflow.toml").write_text(toml)
    subprocess.run(["git", "add", "."], cwd=hub, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=hub, check=True)
    (hub / "secret.pem").write_text("KEY\n")
    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "stdout": "x", "report": {"status": "done", "summary": "ok"}},
        name="link.json"))
    beads = beads_mod.FakeBeads([beads_mod.Bead(
        id="b1", kind="impl", files=["src/b1/"],
        test="mkdir -p src/b1 && echo ok > src/b1/a")])
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    assert rc == 0
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        cwd=hub / ".claude" / "worktrees" / "b1", check=True,
        capture_output=True, text=True).stdout.splitlines()
    assert "secret.pem" not in tree


def test_exit_code_is_highest_per_bead(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([
        make_bead("b1", files=["src/a/"], test="false"),
        make_bead("b2", files=["src/b/"], test="true"),
    ])
    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "stdout": "x", "session_id": "s1",
                   "report": {"status": "done", "summary": "s"}}))
    rc = run_mod.run_many(["b1", "b2"], hub=hub, beads=beads,
                          config=config_mod.load(hub),
                          harness_override="fake", max_parallel=2)
    assert rc == 5


def test_launched_event_has_null_session(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "stdout": "x", "session_id": "s1",
                   "report": {"status": "done", "summary": "s"}}))
    assert run_mod.run_one("b1", hub=hub, beads=beads,
                           config=config_mod.load(hub),
                           harness_override="fake") == 0
    events = [json.loads(line) for line in
              (hub / ".helios" / "events.jsonl").read_text().splitlines()]
    assert [e["type"] for e in events] == ["launched", "completed", "finalized"]
    assert events[0]["session"] is None
    assert read_envelope(hub, "b1", 1)["session_id"] == "s1"


def test_harness_get_errors() -> None:
    from helios.harness import get as _get

    with pytest.raises(TypeError):
        _get("base")
    for name in ("nope", "fake.x", "__init__", "claude"):
        with pytest.raises(ValueError, match=name.split(".")[0]):
            _get(name)
