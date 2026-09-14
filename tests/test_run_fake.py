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


def make_bead(bead_id: str = "b1", **kw: object) -> beads_mod.Bead:
    fields: dict[str, object] = {
        "id": bead_id, "kind": "impl", "files": ["src/"], "test": "true",
        "docs": [], "memories": [],
    }
    fields.update(kw)
    return beads_mod.Bead(
        id=fields["id"] if isinstance(fields["id"], str) else bead_id,
        kind=fields["kind"] if isinstance(fields["kind"], str) else "impl",
        files=list(fields["files"]) if isinstance(fields["files"], list) else ["src/"],
        test=fields["test"] if isinstance(fields["test"], str) else "true",
        docs=list(fields["docs"]) if isinstance(fields["docs"], list) else [],
        memories=list(fields["memories"]) if isinstance(fields["memories"], list) else [],
    )


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


def test_b_report_file_overflow_number_is_invalid_output(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 0, "stdout": "x", "session_id": "s1",
         "report": None, "report_text": '{"status": "done", "summary": 1e999}'},
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
import json, os, sys, time
from pathlib import Path
from helios import beads as B, config as C, run as R
hub = Path(os.environ["HELIOS_CHILD_HUB"])
ids = os.environ["HELIOS_CHILD_BEADS"].split(",")
tests = json.loads(os.environ.get("HELIOS_CHILD_TESTS", "{}"))
slow = float(os.environ.get("HELIOS_CHILD_SLOW", "0"))
if slow:
    _orig = R._assemble_inputs
    def _slow(**kw):
        time.sleep(slow)
        return _orig(**kw)
    R._assemble_inputs = _slow
beads = B.FakeBeads(
    [B.Bead(id=i, kind="impl", files=[f"src/{i}/"], test=tests.get(i, "true")) for i in ids]
)
rc = R.run_many(ids, hub=hub, beads=beads, config=C.load(hub),
                harness_override="fake", max_parallel=int(os.environ["HELIOS_CHILD_MAXP"]))
sys.exit(rc)
"""


def spawn_child(hub: Path, script: Path, bead_ids: str, maxp: int = 1,
                tests: dict | None = None, slow: float = 0):
    """Run helios in a real subprocess so a real SIGINT can be delivered."""
    env = dict(os.environ, HELIOS_FAKE_SCRIPT=str(script),
               HELIOS_CHILD_HUB=str(hub), HELIOS_CHILD_BEADS=bead_ids,
               HELIOS_CHILD_MAXP=str(maxp),
               HELIOS_CHILD_TESTS=json.dumps(tests or {}),
               HELIOS_CHILD_SLOW=str(slow))
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

    for name in ("base", "nope", "fake.x", "__init__"):
        with pytest.raises(ValueError, match=name.split(".")[0]):
            _get(name)


def test_harness_get_real_adapters() -> None:
    from helios.harness import get as _get

    for name, class_name in (
        ("claude", "ClaudeAdapter"),
        ("codex", "CodexAdapter"),
        ("opencode", "OpencodeAdapter"),
        ("agy", "AgyAdapter"),
    ):
        harness = _get(name)
        assert type(harness).__name__ == class_name
        assert harness.name == name


def group_dead(pgid: int | None) -> bool:
    if not pgid:
        return True
    for _ in range(40):
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.05)
    return False


def reap_group(pgid: int | None) -> None:
    if pgid:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def test_sigint_during_checks_single(tmp_path: Path) -> None:
    hub = make_hub(tmp_path)
    script = write_script(tmp_path, {"report": {"status": "done", "summary": "ok"}})
    proc = spawn_child(hub, script, "b1", tests={"b1": "sleep 6"})
    wait_for(hub / ".helios" / "runs" / "b1" / "attempt-1" / "state.log",
             "native_completed")
    time.sleep(0.7)
    os.kill(proc.pid, signal.SIGINT)
    proc.communicate(timeout=90)
    assert proc.returncode == 4
    env = read_envelope(hub, "b1", 1)
    assert env["execution_status"] == "completed"
    assert attempt_mod.read_state(
        hub / ".helios" / "runs" / "b1" / "attempt-1")["state"] == "finalized"


def test_sigint_during_checks_parallel(tmp_path: Path) -> None:
    hub = make_hub(tmp_path)
    script = write_script(tmp_path, {"report": {"status": "done", "summary": "ok"}})
    proc = spawn_child(hub, script, "b1,b2", maxp=2,
                       tests={"b1": "sleep 6", "b2": "sleep 6"})
    for bid in ("b1", "b2"):
        wait_for(hub / ".helios" / "runs" / bid / "attempt-1" / "state.log",
                 "native_completed")
    time.sleep(0.7)
    os.kill(proc.pid, signal.SIGINT)
    proc.communicate(timeout=90)
    assert proc.returncode == 4
    for bid in ("b1", "b2"):
        assert attempt_mod.read_state(
            hub / ".helios" / "runs" / bid / "attempt-1")["state"] == "finalized"


def test_second_sigint_still_exits_4(tmp_path: Path) -> None:
    hub = make_hub(tmp_path)
    script = write_script(
        tmp_path,
        {"sleep_s": 40, "stdout": "slow",
         "report": {"status": "done", "summary": "ok"}, "ignore_sigint": True},
    )
    proc = spawn_child(hub, script, "b1")
    a1 = hub / ".helios" / "runs" / "b1" / "attempt-1"
    wait_for(a1 / "stdout.jsonl", "slow")
    pid = attempt_mod.read_state(a1)["pid"]
    os.kill(proc.pid, signal.SIGINT)
    time.sleep(2)
    os.kill(proc.pid, signal.SIGINT)
    proc.communicate(timeout=90)
    alive = not group_dead(pid)
    reap_group(pid)
    assert not alive and proc.returncode == 4
    assert read_envelope(hub, "b1", 1)["execution_status"] == "interrupted"


def test_queued_bead_never_launches(tmp_path: Path) -> None:
    hub = make_hub(tmp_path)
    script = write_script(
        tmp_path,
        {"sleep_s": 5, "stdout": "slow", "report": {"status": "done", "summary": "ok"}},
    )
    proc = spawn_child(hub, script, "b1,b2,b3", maxp=2)
    for bid in ("b1", "b2"):
        wait_for(hub / ".helios" / "runs" / bid / "attempt-1" / "stdout.jsonl", "slow")
    os.kill(proc.pid, signal.SIGINT)
    proc.communicate(timeout=90)
    assert proc.returncode == 4
    assert not (hub / ".helios" / "runs" / "b3").exists()


GRANDCHILD = r"""
import os, signal, subprocess, sys, time
mode, gcfile = sys.argv[1], sys.argv[2]
gc = ("import os,signal,time,sys; signal.signal(signal.SIGINT, signal.SIG_IGN); "
      "signal.signal(signal.SIGTERM, signal.SIG_IGN); open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(120)")
subprocess.Popen([sys.executable, "-c", gc, gcfile])
if mode == "escalate":
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
while not os.path.exists(gcfile):
    time.sleep(0.02)
print("slow", flush=True)
time.sleep(120)
"""


@pytest.mark.parametrize("mode", ["escalate", "leader_exits"])
def test_timeout_waits_for_whole_group(tmp_path: Path, monkeypatch, mode: str) -> None:
    from helios.harness import fake as fake_mod

    hub = make_hub(tmp_path)
    gcfile = tmp_path / "gc.pid"
    monkeypatch.setattr(
        fake_mod.FakeHarness, "argv",
        lambda self, spec: [sys.executable, "-c", GRANDCHILD, mode, str(gcfile)])
    set_fake(monkeypatch, write_script(tmp_path, {}))
    rc = run_mod.run_one("b1", hub=hub,
                         beads=beads_mod.FakeBeads([make_bead("b1")]),
                         config=config_mod.load(hub), harness_override="fake",
                         timeout_s=3)
    pgid = attempt_mod.read_state(hub / ".helios" / "runs" / "b1" / "attempt-1")["pid"]
    alive = not group_dead(pgid)
    reap_group(pgid)
    assert rc == 4
    assert read_envelope(hub, "b1", 1)["execution_status"] == "timed_out"
    assert not alive


def setup_attempt(hub: Path, state: str, pid=None):
    cfg = config_mod.load(hub)
    info = worktree_mod.prepare(hub=hub, bead="b1")
    att, _ = attempt_mod.allocate(
        hub=hub, runs_rel=cfg.project.runs, bead="b1", worktree=info.path)
    (att.dir / "input.json").write_text(json.dumps(
        {"input_hashes": {"x": "y"}, "base_commit": info.base_commit}))
    if state in ("native_completed", "validated", "invalid"):
        rp = attempt_mod.worktree_report_path(info.path, 1)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps({"status": "done", "summary": "ok"}))
        (info.path / "src/b1").mkdir(parents=True)
        (info.path / "src/b1/x.txt").write_text("x\n")
        (att.dir / "stdout.jsonl").write_text("ok\n")
    if state != "allocated":
        attempt_mod.transition(att.dir, "launched", pid=pid)
        if state != "launched":
            if state in ("validated", "invalid"):
                attempt_mod.transition(att.dir, "native_completed")
            attempt_mod.transition(att.dir, state)
    return cfg, info, att


def dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


RECOVERY_STATES = ["allocated", "launched", "native_completed", "validated", "invalid",
                   "interrupted", "timed_out", "crashed", "launch_failed"]


@pytest.mark.parametrize("state", RECOVERY_STATES)
def test_recovery_matrix(tmp_path: Path, monkeypatch, state: str) -> None:
    hub = make_hub(tmp_path)
    cfg, info, att = setup_attempt(hub, state, pid=dead_pid())
    before = [json.loads(line)["state"]
              for line in (att.dir / "state.log").read_text().splitlines()]
    set_fake(monkeypatch, write_script(
        tmp_path, {"report": {"status": "done", "summary": "ok"}}))
    beads = beads_mod.FakeBeads([beads_mod.Bead(
        id="b1", kind="impl", files=["src/b1/"],
        test="mkdir -p src/b1 && echo y > src/b1/y.txt")])
    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg,
                         harness_override="fake")
    after = [json.loads(line)["state"]
             for line in (att.dir / "state.log").read_text().splitlines()]
    attempts = attempt_mod.existing_attempts(hub / ".helios" / "runs" / "b1")
    assert after[-1] == "finalized"
    if state in ("native_completed", "validated", "invalid"):
        assert attempts == [1]
        assert (att.dir / "envelope.json").exists()
    elif state in ("allocated", "launched"):
        assert after[len(before):] == ["crashed", "finalized"] and attempts == [1, 2]
    else:
        assert after[len(before):] == ["finalized"] and attempts == [1, 2]
    assert rc in (0, 3, 4, 5)


@pytest.mark.parametrize("flag", ["interrupted", "timed_out"])
def test_resume_keeps_terminal_status(tmp_path: Path, monkeypatch, flag: str) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    set_fake(monkeypatch, write_script(
        tmp_path, {"report": {"status": "done", "summary": "ok"}}))
    real_law = run_mod._launch_and_wait

    def fake_law(*a, **k):
        code, _t, _i, lf, pid = real_law(*a, **k)
        return code, flag == "timed_out", flag == "interrupted", lf, pid

    monkeypatch.setattr(run_mod, "_launch_and_wait", fake_law)
    real_wb = beads_mod.apply_writeback

    def boom(*a, **k):
        raise RuntimeError("bd died")

    monkeypatch.setattr(beads_mod, "apply_writeback", boom)
    beads = beads_mod.FakeBeads([beads_mod.Bead(
        id="b1", kind="impl", files=["src/b1/"],
        test="mkdir -p src/b1 && echo y > src/b1/y")])
    with pytest.raises(RuntimeError):
        run_mod.run_one("b1", hub=hub, beads=beads, config=cfg,
                        harness_override="fake")
    first = read_envelope(hub, "b1", 1)["execution_status"]
    monkeypatch.setattr(beads_mod, "apply_writeback", real_wb)
    monkeypatch.setattr(run_mod, "_launch_and_wait", real_law)
    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg,
                         harness_override="fake")
    assert first == flag
    assert read_envelope(hub, "b1", 1)["execution_status"] == flag
    assert rc == 4 and "b1" not in beads.closed


def test_pre_staged_unowned_never_committed(
    tmp_path: Path, monkeypatch
) -> None:
    hub = make_hub(tmp_path)
    cmd = ("echo evil >> README.md && git add README.md && git show HEAD:README.md > README.md "
           "&& mkdir -p src/b1 && echo ok > src/b1/a")
    set_fake(monkeypatch, write_script(
        tmp_path, {"report": {"status": "done", "summary": "ok"}}))
    beads = beads_mod.FakeBeads([beads_mod.Bead(
        id="b1", kind="impl", files=["src/b1/"], test=cmd)])
    run_mod.run_one("b1", hub=hub, beads=beads,
                    config=config_mod.load(hub), harness_override="fake")
    wt = hub / ".claude" / "worktrees" / "b1"
    assert subprocess.run(
        ["git", "show", "HEAD:README.md"], cwd=wt, check=True,
        capture_output=True, text=True).stdout == "hi\n"


def test_pre_staged_confidential_never_committed(
    tmp_path: Path, monkeypatch
) -> None:
    hub = make_hub(tmp_path)
    (hub / ".agents").mkdir(exist_ok=True)
    (hub / ".agents" / "workflow.toml").write_text(
        '[project]\nconfidential = ["*.pem"]\n')
    subprocess.run(["git", "add", "."], cwd=hub, check=True)
    subprocess.run(["git", "commit", "-qm", "conf"], cwd=hub, check=True)
    cmd = "echo KEY > k.pem && git add k.pem && rm k.pem && mkdir -p src/b1 && echo ok > src/b1/a"
    set_fake(monkeypatch, write_script(
        tmp_path, {"report": {"status": "done", "summary": "ok"}}))
    beads = beads_mod.FakeBeads([beads_mod.Bead(
        id="b1", kind="impl", files=["src/b1/"], test=cmd)])
    run_mod.run_one("b1", hub=hub, beads=beads,
                    config=config_mod.load(hub), harness_override="fake")
    wt = hub / ".claude" / "worktrees" / "b1"
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=wt, check=True,
        capture_output=True, text=True).stdout.splitlines()
    assert "k.pem" not in tree


def test_glob_pathspec_does_not_stage_link(tmp_path: Path, monkeypatch) -> None:
    hub = tmp_path / "hub"
    hub.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=hub, check=True)
    (hub / "README.md").write_text("hi\n")
    (hub / ".gitignore").write_text(".helios/\n.claude/worktrees/\n")
    (hub / "skills" / "impl").mkdir(parents=True)
    (hub / "skills" / "impl" / "SKILL.md").write_text("---\nname: impl\n---\n\nDo.\n")
    (hub / "AGENTS.md").write_text("# h\n\n## Worker contract\n\nOne bead.\n")
    (hub / ".agents").mkdir()
    (hub / ".agents" / "workflow.toml").write_text(
        '[project]\nlink_into_worktrees = ["src/b1/.env"]\nconfidential = [".env"]\n')
    (hub / "src" / "b1").mkdir(parents=True)
    (hub / "src" / "b1" / "keep").write_text("k\n")
    subprocess.run(["git", "add", "."], cwd=hub, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=hub, check=True)
    (hub / "src" / "b1" / ".env").write_text("SECRET=1\n")
    set_fake(monkeypatch, write_script(
        tmp_path, {"report": {"status": "done", "summary": "ok"}}))
    beads = beads_mod.FakeBeads([beads_mod.Bead(
        id="b1", kind="impl", files=["src/b1/"],
        test="echo x > 'src/b1/[.]env'")])
    run_mod.run_one("b1", hub=hub, beads=beads,
                    config=config_mod.load(hub), harness_override="fake")
    wt = hub / ".claude" / "worktrees" / "b1"
    assert os.path.islink(wt / "src/b1" / ".env")
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=wt, check=True,
        capture_output=True, text=True).stdout.splitlines()
    assert "src/b1/.env" not in tree


def test_staging_delete_and_rename(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    (hub / "src").mkdir()
    (hub / "src" / "b1").mkdir()
    (hub / "src" / "b1" / "old.txt").write_text("o\n")
    (hub / "src" / "b1" / "keep.txt").write_text("k\n")
    subprocess.run(["git", "add", "."], cwd=hub, check=True)
    subprocess.run(["git", "commit", "-qm", "files"], cwd=hub, check=True)
    set_fake(monkeypatch, write_script(
        tmp_path, {"report": {"status": "done", "summary": "ok"}}))
    beads = beads_mod.FakeBeads([beads_mod.Bead(
        id="b1", kind="impl", files=["src/b1/"],
        test="rm src/b1/keep.txt && mv src/b1/old.txt src/b1/new.txt")])
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    wt = hub / ".claude" / "worktrees" / "b1"
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=wt, check=True,
        capture_output=True, text=True).stdout.splitlines()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=wt, check=True,
        capture_output=True, text=True).stdout
    assert rc == 0 and "src/b1/new.txt" in tree
    assert "src/b1/old.txt" not in tree and "src/b1/keep.txt" not in tree
    assert status == ""


@pytest.mark.parametrize("payload,want,rc_want", [
    ({"report": {"status": "done", "summary": "ok"}, "exit_code": 9}, "completed", 0),
    ({"report_text": "{nope", "exit_code": 9}, "invalid_output", 4),
    ({"report": {"status": "done"}}, "invalid_output", 4),
    ({"exit_code": 9}, "crashed", 4),
    ({}, "missing_output", 4),
])
def test_classification_matrix(tmp_path: Path, monkeypatch, payload, want, rc_want) -> None:
    hub = make_hub(tmp_path)
    set_fake(monkeypatch, write_script(tmp_path, dict(payload)))
    beads = beads_mod.FakeBeads([make_bead("b1")])
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    env = read_envelope(hub, "b1", 1)
    envelope_mod.Envelope.model_validate(env)
    assert env["execution_status"] == want and rc == rc_want
    if want == "completed":
        assert any("exit" in n for n in env["notes"])


def test_set_state_called_once(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1", test="false")])
    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "stdout": "x", "session_id": "s1",
                   "report": {"status": "done", "summary": "s"}}))
    assert run_mod.run_one("b1", hub=hub, beads=beads,
                           config=config_mod.load(hub),
                           harness_override="fake") == 5
    sets = [a for a in beads.argv_log if a and a[0] == "set-state"]
    assert len(sets) == 1 and sets[0][3] == "failed"


def test_writeback_replay_keeps_comments(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    rep = {"status": "needs_input", "summary": "q", "question": "why?",
           "learned": ["l1", "l2"], "missing_context": ["m1"], "followups": ["f1"]}
    set_fake(monkeypatch, write_script(
        tmp_path, {"report": rep, "session_id": "s1"}))
    beads = beads_mod.FakeBeads([make_bead("b1")])
    run_mod.run_one("b1", hub=hub, beads=beads,
                    config=config_mod.load(hub), harness_override="fake")
    first = [c.text for c in beads.comments("b1")]
    a1 = hub / ".helios" / "runs" / "b1" / "attempt-1"
    attempt_mod.transition(a1, "validated")
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    second = [c.text for c in beads.comments("b1")]
    assert first == second and len(first) == 6 and rc == 3


def test_stdin_devnull_or_text(tmp_path: Path, monkeypatch) -> None:
    from helios.harness import fake as fake_mod

    child = ("import os, sys; st = os.fstat(0); dn = os.stat('/dev/null'); "
             "data = sys.stdin.read(); "
             "open(sys.argv[1], 'w').write(repr((os.path.samestat(st, dn), data)))")
    for text in (None, "PROMPT-X"):
        hub = make_hub(tmp_path / ("s" if text is None else "t"))
        outf = tmp_path / ("o1" if text is None else "o2")
        monkeypatch.setattr(
            fake_mod.FakeHarness, "argv",
            lambda self, spec: [sys.executable, "-c", child, str(outf)])
        monkeypatch.setattr(fake_mod.FakeHarness, "stdin_text",
                            lambda self, spec: text)
        set_fake(monkeypatch, write_script(tmp_path, {}))
        run_mod.run_one("b1", hub=hub, beads=beads_mod.FakeBeads([make_bead("b1")]),
                        config=config_mod.load(hub), harness_override="fake",
                        timeout_s=20)
        assert outf.read_text() == repr((text is None, text or ""))


def test_live_refusal_message_and_lock(tmp_path: Path, monkeypatch, capsys) -> None:
    hub = make_hub(tmp_path)
    live = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        setup_attempt(hub, "launched", pid=live.pid)
        set_fake(monkeypatch, write_script(
            tmp_path, {"report": {"status": "done", "summary": "ok"}}))
        beads = beads_mod.FakeBeads([make_bead("b1")])
        rc = run_mod.run_many(["b1"], hub=hub, beads=beads,
                              config=config_mod.load(hub), harness_override="fake")
        out = capsys.readouterr()
        assert rc == 2
        assert "helios attach b1" in out.out + out.err
        assert "helios stop b1" in out.out + out.err
        assert beads.argv_log == []
    finally:
        live.kill()
        live.wait()


def test_sigint_during_preflight_creates_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    import threading as _threading

    from helios import preflight as preflight_mod

    hub = make_hub(tmp_path)
    set_fake(monkeypatch, write_script(
        tmp_path, {"report": {"status": "done", "summary": "ok"}}))
    real_check = preflight_mod.check

    def slow_check(*a, **k):
        time.sleep(3)
        return real_check(*a, **k)

    monkeypatch.setattr(preflight_mod, "check", slow_check)
    beads = beads_mod.FakeBeads([
        make_bead("b1", files=["src/a/"]),
        make_bead("b2", files=["src/b/"]),
    ])
    timer = _threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    try:
        rc = run_mod.run_many(["b1", "b2"], hub=hub, beads=beads,
                              config=config_mod.load(hub),
                              harness_override="fake", max_parallel=2)
    finally:
        timer.cancel()
    assert rc == 4
    assert not (hub / ".helios").exists()
    assert not (hub / ".claude" / "worktrees").exists()
    assert beads.argv_log == []
    beads2 = beads_mod.FakeBeads([
        make_bead("b1", files=["src/a/"]),
        make_bead("b2", files=["src/b/"]),
    ])
    assert run_mod.run_many(["b1", "b2"], hub=hub, beads=beads2,
                            config=config_mod.load(hub),
                            harness_override="fake", max_parallel=2) == 0


def test_handler_restored_and_flag_cleared(tmp_path: Path, monkeypatch) -> None:
    import signal as _signal
    import threading as _threading

    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "stdout": "x", "session_id": "s1",
                   "report": {"status": "done", "summary": "s"}}))
    before = _signal.getsignal(_signal.SIGINT)
    assert run_mod.run_many(["b1"], hub=hub, beads=beads,
                            config=config_mod.load(hub),
                            harness_override="fake") == 0
    assert _signal.getsignal(_signal.SIGINT) is before
    assert not [t for t in _threading.enumerate()
                if t.name.startswith("_stopper") and t.is_alive()]


def test_sigint_during_prepare_no_launch(tmp_path: Path) -> None:
    hub = make_hub(tmp_path)
    script = write_script(
        tmp_path, {"report": {"status": "done", "summary": "ok"}, "stdout": "ran"})
    proc = spawn_child(hub, script, "b1", slow=3)
    time.sleep(1.0)
    os.kill(proc.pid, signal.SIGINT)
    proc.communicate(timeout=90)
    assert proc.returncode == 4
    a1 = hub / ".helios" / "runs" / "b1" / "attempt-1"
    assert not (a1 / "stdout.jsonl").exists()
    if a1.exists():
        assert "launched" not in [
            json.loads(line)["state"] for line in (a1 / "state.log").read_text().splitlines()]
    quick = spawn_child(
        hub, write_script(tmp_path, {"report": {"status": "done", "summary": "ok"}},
                          name="q.json"), "b1")
    quick.communicate(timeout=60)
    assert quick.returncode == 0


@pytest.mark.parametrize("name,extra,cmd,present,absent", [
    ("staged_then_deleted_owned", {},
     "mkdir -p src/b1 && echo t > src/b1/tmp && git add src/b1/tmp && rm src/b1/tmp && echo ok > src/b1/a",
     ["src/b1/a"], ["src/b1/tmp"]),
    ("git_mv_git_rm", {"src/b1/old.txt": "o\n", "src/b1/gone.txt": "g\n"},
     "git mv src/b1/old.txt src/b1/new.txt && git rm -q src/b1/gone.txt",
     ["src/b1/new.txt"], ["src/b1/old.txt", "src/b1/gone.txt"]),
])
def test_commit_variants(tmp_path: Path, monkeypatch, name, extra, cmd, present, absent) -> None:
    hub = make_hub(tmp_path)
    if extra:
        for rel, text in extra.items():
            (hub / rel).parent.mkdir(parents=True, exist_ok=True)
            (hub / rel).write_text(text)
        subprocess.run(["git", "add", "."], cwd=hub, check=True)
        subprocess.run(["git", "commit", "-qm", "extra"], cwd=hub, check=True)
    set_fake(monkeypatch, write_script(
        tmp_path, {"report": {"status": "done", "summary": "ok"}}))
    beads = beads_mod.FakeBeads([beads_mod.Bead(
        id="b1", kind="impl", files=["src/b1/"], test=cmd)])
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    wt = hub / ".claude" / "worktrees" / "b1"
    env = read_envelope(hub, "b1", 1)
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=wt, check=True,
        capture_output=True, text=True).stdout.splitlines()
    assert rc == 0 and env["output_commit"] is not None and "b1" in beads.closed
    assert all(x in tree for x in present) and not any(x in tree for x in absent)
    _ = name


def test_stored_completed_survives_worktree_tamper(
    tmp_path: Path, monkeypatch
) -> None:
    for tamper in ("delete", "garbage"):
        hub = make_hub(tmp_path / tamper)
        set_fake(monkeypatch, write_script(
            tmp_path / tamper, {"report": {"status": "done", "summary": "ok"}}))
        real_wb = beads_mod.apply_writeback

        def boom(*a, **k):
            raise RuntimeError("bd died")

        monkeypatch.setattr(beads_mod, "apply_writeback", boom)
        beads = beads_mod.FakeBeads([beads_mod.Bead(
            id="b1", kind="impl", files=["src/b1/"],
            test="mkdir -p src/b1 && echo y > src/b1/y")])
        with pytest.raises(RuntimeError):
            run_mod.run_one("b1", hub=hub, beads=beads,
                            config=config_mod.load(hub), harness_override="fake")
        assert read_envelope(hub, "b1", 1)["execution_status"] == "completed"
        rp = attempt_mod.worktree_report_path(hub / ".claude" / "worktrees" / "b1", 1)
        if tamper == "delete":
            rp.unlink()
        else:
            rp.write_text("{garbage")
        monkeypatch.setattr(beads_mod, "apply_writeback", real_wb)
        rc = run_mod.run_one("b1", hub=hub, beads=beads,
                             config=config_mod.load(hub), harness_override="fake")
        assert read_envelope(hub, "b1", 1)["execution_status"] == "completed"
        assert rc == 0 and "b1" in beads.closed


@pytest.mark.parametrize("tamper", ["delete", "garbage"])
def test_stored_completed_bad_captured_fails_report_check(
    tmp_path: Path, monkeypatch, tamper: str
) -> None:
    hub = make_hub(tmp_path)
    set_fake(monkeypatch, write_script(
        tmp_path, {"report": {"status": "done", "summary": "ok"}}))
    real_wb = beads_mod.apply_writeback

    def boom(*a, **k):
        raise RuntimeError("bd died")

    monkeypatch.setattr(beads_mod, "apply_writeback", boom)
    beads = beads_mod.FakeBeads([beads_mod.Bead(
        id="b1", kind="impl", files=["src/b1/"],
        test="mkdir -p src/b1 && echo y > src/b1/y")])
    with pytest.raises(RuntimeError):
        run_mod.run_one("b1", hub=hub, beads=beads,
                        config=config_mod.load(hub), harness_override="fake")
    cap = hub / ".helios" / "runs" / "b1" / "attempt-1" / "report.json"
    if tamper == "delete":
        cap.unlink()
    else:
        cap.write_text("{garbage")
    monkeypatch.setattr(beads_mod, "apply_writeback", real_wb)
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    env = json.loads((hub / ".helios" / "runs" / "b1" / "attempt-1" / "envelope.json").read_text())
    assert env["execution_status"] == "completed" and rc == 5
    assert "b1" not in beads.closed
    assert any(c["name"] == "report" and not c["passed"] for c in env["checks"])


def test_state_json_has_six_keys(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "stdout": "x", "session_id": "s1",
                   "report": {"status": "done", "summary": "s"}}))
    beads = beads_mod.FakeBeads([make_bead("b1")])
    assert run_mod.run_one("b1", hub=hub, beads=beads,
                           config=config_mod.load(hub),
                           harness_override="fake") == 0
    state = attempt_mod.read_state(hub / ".helios" / "runs" / "b1" / "attempt-1")
    assert set(state) == {"state", "attempt_id", "pid", "session_id",
                          "execution_status", "updated"}
    assert state["execution_status"] == "completed"


def test_empty_attempt_dir_recovers(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    (hub / ".helios" / "runs" / "b1" / "attempt-1").mkdir(parents=True)
    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "stdout": "x", "session_id": "s1",
                   "report": {"status": "done", "summary": "s"}}))
    beads = beads_mod.FakeBeads([make_bead("b1")])
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    assert rc == 0
    assert attempt_mod.existing_attempts(hub / ".helios" / "runs" / "b1") == [1, 2]
    log = [json.loads(line)["state"] for line in
           (hub / ".helios" / "runs" / "b1" / "attempt-1" / "state.log").read_text().splitlines()]
    assert log == ["crashed", "finalized"]
    assert read_envelope(hub, "b1", 2)["execution_status"] == "completed"


def test_commit_owned_variants(tmp_path: Path, monkeypatch) -> None:
    cases = {
        "combined": (
            {"src/b1/old.txt": "o\n", "src/b1/gone.txt": "g\n", "src/b1/a": "a\n"},
            "git mv src/b1/old.txt src/b1/new.txt && git rm -q src/b1/gone.txt && "
            "echo t > src/b1/tmp && git add src/b1/tmp && rm src/b1/tmp && echo edited > src/b1/a",
            {("D", "src/b1/old.txt"), ("A", "src/b1/new.txt"),
             ("D", "src/b1/gone.txt"), ("M", "src/b1/a")},
        ),
        "special_names": (
            {"src/b1/o l d é.txt": "o\n", "src/b1/g*o?ne[1].txt": "g\n",
             "src/b1/keep*.txt": "k\n"},
            "git mv 'src/b1/o l d é.txt' 'src/b1/[ab] n*é?.txt' && "
            "git rm -q 'src/b1/g*o?ne[1].txt' && echo new > 'src/b1/:(glob)x*'",
            {("D", "src/b1/o l d é.txt"), ("A", "src/b1/[ab] n*é?.txt"),
             ("D", "src/b1/g*o?ne[1].txt"), ("A", "src/b1/:(glob)x*")},
        ),
        "rm_r_dir": (
            {"src/b1/d/x": "1\n", "src/b1/d/sub/y": "2\n", "src/b1/keep": "k\n"},
            "git rm -r -q src/b1/d",
            {("D", "src/b1/d/x"), ("D", "src/b1/d/sub/y")},
        ),
        "staged_nonascii_deleted": (
            {"src/b1/a": "a\n"},
            "mkdir -p src/b1 && echo t > 'src/b1/tmp é*' && git add 'src/b1/tmp é*' && "
            "rm 'src/b1/tmp é*' && echo e > src/b1/a",
            {("M", "src/b1/a")},
        ),
    }
    for name, (extra, cmd, want) in cases.items():
        hub = make_hub(tmp_path / name)
        if extra:
            for rel, text in extra.items():
                (hub / rel).parent.mkdir(parents=True, exist_ok=True)
                (hub / rel).write_text(text)
            subprocess.run(["git", "add", "."], cwd=hub, check=True)
            subprocess.run(["git", "commit", "-qm", "extra"], cwd=hub, check=True)
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=hub, check=True,
            capture_output=True, text=True).stdout.strip()
        set_fake(monkeypatch, write_script(
            tmp_path / name, {"report": {"status": "done", "summary": "ok"}}))
        beads = beads_mod.FakeBeads([beads_mod.Bead(
            id="b1", kind="impl", files=["src/b1/"], test=cmd)])
        rc = run_mod.run_one("b1", hub=hub, beads=beads,
                             config=config_mod.load(hub), harness_override="fake")
        wt = hub / ".claude" / "worktrees" / "b1"
        env = read_envelope(hub, "b1", 1)
        raw = subprocess.run(
            ["git", "show", "-z", "--no-renames", "--name-status", "--format=", "HEAD"],
            cwd=wt, check=True, capture_output=True).stdout.decode()
        toks = [t for t in raw.split("\0") if t]
        got = set(zip(toks[0::2], toks[1::2]))
        commits = subprocess.run(
            ["git", "rev-list", f"{base}..HEAD"], cwd=wt, check=True,
            capture_output=True, text=True).stdout.splitlines()
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=wt, check=True,
            capture_output=True, text=True).stdout.strip()
        assert rc == 0 and "b1" in beads.closed, name
        assert len(commits) == 1 and got == want, name
        assert env["output_commit"] == head, name


def test_commit_ownership_across_boundary(tmp_path: Path, monkeypatch) -> None:
    cases = [
        ("dest_outside", {"src/b1/old.txt": "o\n"},
         "mkdir -p other && git mv src/b1/old.txt other/new.txt", "other/new.txt"),
        ("agent_commits_unowned", {},
         "echo evil > README.md && git commit -qam evil && mkdir -p src/b1 && echo y > src/b1/y",
         "README.md"),
    ]
    for name, extra, cmd, bad in cases:
        hub = make_hub(tmp_path / name)
        for rel, text in extra.items():
            (hub / rel).parent.mkdir(parents=True, exist_ok=True)
            (hub / rel).write_text(text)
        if extra:
            subprocess.run(["git", "add", "."], cwd=hub, check=True)
            subprocess.run(["git", "commit", "-qm", "extra"], cwd=hub, check=True)
        set_fake(monkeypatch, write_script(
            tmp_path / name, {"report": {"status": "done", "summary": "ok"}}))
        beads = beads_mod.FakeBeads([beads_mod.Bead(
            id="b1", kind="impl", files=["src/b1/"], test=cmd)])
        rc = run_mod.run_one("b1", hub=hub, beads=beads,
                             config=config_mod.load(hub), harness_override="fake")
        env = read_envelope(hub, "b1", 1)
        own = [c for c in env["checks"] if c["name"] == "ownership"][0]
        assert rc == 5 and not own["passed"] and bad in own["detail"], name
        assert env["output_commit"] is None and not beads.closed, name
        assert beads.states["b1"]["run"] == "failed", name


def test_commit_agent_partial(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=hub, check=True,
        capture_output=True, text=True).stdout.strip()
    cmd = ("mkdir -p src/b1 && echo 1 > src/b1/p && git add src/b1/p && git commit -qm part && "
           "echo 2 > src/b1/q && git add src/b1/q && echo 3 > src/b1/r")
    set_fake(monkeypatch, write_script(
        tmp_path, {"report": {"status": "done", "summary": "ok"}}))
    beads = beads_mod.FakeBeads([beads_mod.Bead(
        id="b1", kind="impl", files=["src/b1/"], test=cmd)])
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    wt = hub / ".claude" / "worktrees" / "b1"
    env = read_envelope(hub, "b1", 1)
    commits = subprocess.run(
        ["git", "rev-list", f"{base}..HEAD"], cwd=wt, check=True,
        capture_output=True, text=True).stdout.splitlines()
    raw = subprocess.run(
        ["git", "show", "-z", "--no-renames", "--name-status", "--format=", "HEAD"],
        cwd=wt, check=True, capture_output=True).stdout.decode()
    toks = [t for t in raw.split("\0") if t]
    assert rc == 0 and len(commits) == 2
    assert set(zip(toks[0::2], toks[1::2])) == {("A", "src/b1/q"), ("A", "src/b1/r")}
    assert env["output_commit"] == subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=wt, check=True,
        capture_output=True, text=True).stdout.strip()
    assert subprocess.run(
        ["git", "status", "--porcelain"], cwd=wt, check=True,
        capture_output=True, text=True).stdout == ""


def test_committed_then_removed_is_committed(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    set_fake(monkeypatch, write_script(
        tmp_path, {"report": {"status": "done", "summary": "ok"}}))
    beads = beads_mod.FakeBeads([beads_mod.Bead(
        id="b1", kind="impl", files=["src/b1/"],
        test="mkdir -p src/b1 && echo 1 > src/b1/p && git add src/b1/p && "
             "git commit -qm part && git rm -q src/b1/p")])
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    wt = hub / ".claude" / "worktrees" / "b1"
    env = read_envelope(hub, "b1", 1)
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=wt, check=True,
        capture_output=True, text=True).stdout.splitlines()
    assert rc == 0 and "src/b1/p" not in tree
    assert env["output_commit"] is not None
    assert subprocess.run(
        ["git", "status", "--porcelain"], cwd=wt, check=True,
        capture_output=True, text=True).stdout == ""


def test_committed_then_removed_unowned_fails(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    (hub / "other").mkdir()
    (hub / "other" / "x.txt").write_text("o\n")
    subprocess.run(["git", "add", "."], cwd=hub, check=True)
    subprocess.run(["git", "commit", "-qm", "extra"], cwd=hub, check=True)
    set_fake(monkeypatch, write_script(
        tmp_path, {"report": {"status": "done", "summary": "ok"}}))
    beads = beads_mod.FakeBeads([beads_mod.Bead(
        id="b1", kind="impl", files=["src/b1/"],
        test="mkdir -p src/b1 && echo y > src/b1/y && git rm -q other/x.txt")])
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    env = read_envelope(hub, "b1", 1)
    assert rc == 5 and env["output_commit"] is None


def test_worktree_tamper_keeps_completed(tmp_path: Path, monkeypatch) -> None:
    for tamper in ("delete", "garbage", "other_valid"):
        hub = make_hub(tmp_path / tamper)
        set_fake(monkeypatch, write_script(
            tmp_path / tamper, {"report": {"status": "done", "summary": "ok"}}))
        real_wb = beads_mod.apply_writeback

        def boom(*a, **k):
            raise RuntimeError("bd died")

        monkeypatch.setattr(beads_mod, "apply_writeback", boom)
        beads = beads_mod.FakeBeads([beads_mod.Bead(
            id="b1", kind="impl", files=["src/b1/"],
            test="mkdir -p src/b1 && echo y > src/b1/y")])
        with pytest.raises(RuntimeError):
            run_mod.run_one("b1", hub=hub, beads=beads,
                            config=config_mod.load(hub), harness_override="fake")
        cap_before = (hub / ".helios" / "runs" / "b1" / "attempt-1" / "report.json").read_bytes()
        rp = attempt_mod.worktree_report_path(hub / ".claude" / "worktrees" / "b1", 1)
        if tamper == "delete":
            rp.unlink()
        elif tamper == "garbage":
            rp.write_text("{garbage")
        else:
            rp.write_text(json.dumps({"status": "blocked", "summary": "evil"}))
        monkeypatch.setattr(beads_mod, "apply_writeback", real_wb)
        rc = run_mod.run_one("b1", hub=hub, beads=beads,
                             config=config_mod.load(hub), harness_override="fake")
        env = read_envelope(hub, "b1", 1)
        assert rc == 0 and env["execution_status"] == "completed", tamper
        assert env["report"]["summary"] == "ok" and "b1" in beads.closed, tamper
        assert (hub / ".helios" / "runs" / "b1" / "attempt-1" / "report.json").read_bytes() == cap_before


@pytest.mark.parametrize("tamper", ["delete", "garbage", "array", "empty",
                                    "schema_invalid", "missing_summary"])
def test_captured_tamper_keeps_completed_fails_report(
    tmp_path: Path, monkeypatch, tamper: str
) -> None:
    hub = make_hub(tmp_path)
    set_fake(monkeypatch, write_script(
        tmp_path, {"report": {"status": "done", "summary": "ok"}}))
    real_wb = beads_mod.apply_writeback

    def boom(*a, **k):
        raise RuntimeError("bd died")

    monkeypatch.setattr(beads_mod, "apply_writeback", boom)
    beads = beads_mod.FakeBeads([beads_mod.Bead(
        id="b1", kind="impl", files=["src/b1/"],
        test="mkdir -p src/b1 && echo y > src/b1/y")])
    with pytest.raises(RuntimeError):
        run_mod.run_one("b1", hub=hub, beads=beads,
                        config=config_mod.load(hub), harness_override="fake")
    cap = hub / ".helios" / "runs" / "b1" / "attempt-1" / "report.json"
    {"delete": lambda: cap.unlink(), "garbage": lambda: cap.write_text("{garbage"),
     "array": lambda: cap.write_text("[]"), "empty": lambda: cap.write_text(""),
     "schema_invalid": lambda: cap.write_text(json.dumps({"status": "bogus", "summary": "x"})),
     "missing_summary": lambda: cap.write_text(json.dumps({"status": "done"}))}[tamper]()
    monkeypatch.setattr(beads_mod, "apply_writeback", real_wb)
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    env = json.loads((hub / ".helios" / "runs" / "b1" / "attempt-1" / "envelope.json").read_text())
    rep = [c for c in env["checks"] if c["name"] == "report"]
    assert env["execution_status"] == "completed", tamper
    assert rep and rep[0]["passed"] is False and rep[0]["detail"], tamper
    assert rc == 5 and "b1" not in beads.closed, tamper
    assert beads.states["b1"]["run"] == "failed", tamper


def test_captured_bytes_identical(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    text = '{ "summary" : "é ok \\u00e9" ,\n\t"status":"done" }\n\n'
    set_fake(monkeypatch, write_script(tmp_path, {"report_text": text}))
    beads = beads_mod.FakeBeads([make_bead("b1")])
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    cap = (hub / ".helios" / "runs" / "b1" / "attempt-1" / "report.json").read_bytes()
    assert rc == 0 and cap == text.encode()


def test_capture_fresh_never_writes_infinity(tmp_path: Path) -> None:
    report_path = tmp_path / "missing-report.json"
    with pytest.raises(ValueError):
        run_mod._capture_fresh({"status": "done", "summary": float("inf")}, report_path)


def test_captured_native_choice_is_dumps(tmp_path: Path, monkeypatch) -> None:
    from helios.harness import fake as fake_mod
    from helios.harness.base import NativeResult

    hub = make_hub(tmp_path)
    native = {"status": "done", "summary": "native"}
    set_fake(monkeypatch, write_script(
        tmp_path, {"report": {"status": "done", "summary": "file"}}))
    monkeypatch.setattr(
        fake_mod.FakeHarness, "parse", lambda self, spec, code, path: NativeResult(
            session_id="s1", structured=dict(native)))
    beads = beads_mod.FakeBeads([make_bead("b1")])
    real_wb = beads_mod.apply_writeback

    def boom(*a, **k):
        raise RuntimeError("bd died")

    monkeypatch.setattr(beads_mod, "apply_writeback", boom)
    with pytest.raises(RuntimeError):
        run_mod.run_one("b1", hub=hub, beads=beads,
                        config=config_mod.load(hub), harness_override="fake")
    cap = (hub / ".helios" / "runs" / "b1" / "attempt-1" / "report.json").read_bytes()
    assert json.loads(cap) == native
    assert cap == (json.dumps(native, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    monkeypatch.setattr(beads_mod, "apply_writeback", real_wb)
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    assert rc == 0
    assert read_envelope(hub, "b1", 1)["report"]["summary"] == "native"


def test_state_survives_two_resumes(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    set_fake(monkeypatch, write_script(
        tmp_path, {"report": {"status": "done", "summary": "ok"}, "session_id": "s7"}))
    count = {"n": 0}
    real_wb = beads_mod.apply_writeback

    def boom(*a, **k):
        if count["n"] < 2:
            count["n"] += 1
            raise RuntimeError("bd died")
        return real_wb(*a, **k)

    monkeypatch.setattr(beads_mod, "apply_writeback", boom)
    beads = beads_mod.FakeBeads([beads_mod.Bead(
        id="b1", kind="impl", files=["src/b1/"],
        test="mkdir -p src/b1 && echo y > src/b1/y")])
    seen = []
    for _ in range(2):
        with pytest.raises(RuntimeError):
            run_mod.run_one("b1", hub=hub, beads=beads,
                            config=config_mod.load(hub), harness_override="fake")
        st = attempt_mod.read_state(hub / ".helios" / "runs" / "b1" / "attempt-1")
        seen.append(st["execution_status"])
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    assert rc == 0 and seen == ["completed", "completed"]
    assert attempt_mod.existing_attempts(hub / ".helios" / "runs" / "b1") == [1]
    assert attempt_mod.read_state(
        hub / ".helios" / "runs" / "b1" / "attempt-1")["execution_status"] == "completed"


def test_transition_keeps_keys() -> None:
    import tempfile as _tf

    with _tf.TemporaryDirectory() as tmp:
        d = Path(tmp) / "b" / "attempt-1"
        attempt_mod.write_state(d, attempt_id="b#1", state="launched", pid=4242,
                                session_id="s9", execution_status=None)
        attempt_mod.transition(d, "native_completed", execution_status="completed")
        attempt_mod.transition(d, "validated")
        attempt_mod.transition(d, "finalized")
        log = [json.loads(x) for x in (d / "state.log").read_text().splitlines()]
    assert all(set(x) == {"state", "attempt_id", "pid", "session_id",
                          "execution_status", "updated"} for x in log)
    assert [x["execution_status"] for x in log] == [None, "completed", "completed", "completed"]


@pytest.mark.parametrize("variant", ["nostate", "empty", "truncated", "array", "no_state_key"])
def test_damaged_state_json_recovers(tmp_path: Path, monkeypatch, variant: str) -> None:
    hub = make_hub(tmp_path)
    info = worktree_mod.prepare(hub=hub, bead="b1")
    d = hub / ".helios" / "runs" / "b1" / "attempt-1"
    d.mkdir(parents=True)
    if variant == "empty":
        (d / "state.json").write_text("")
    elif variant == "truncated":
        (d / "state.json").write_text('{"state": "laun')
    elif variant == "array":
        (d / "state.json").write_text("[]")
    elif variant == "no_state_key":
        (d / "state.json").write_text('{"pid": 1}')
    _ = info
    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "stdout": "x", "session_id": "s1",
                   "report": {"status": "done", "summary": "s"}}))
    beads = beads_mod.FakeBeads([make_bead("b1")])
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    assert rc == 0, variant
    assert attempt_mod.existing_attempts(hub / ".helios" / "runs" / "b1") == [1, 2], variant
    log = [json.loads(line)["state"] for line in
           (d / "state.log").read_text().splitlines()]
    assert log == ["crashed", "finalized"], variant
    assert read_envelope(hub, "b1", 2)["execution_status"] == "completed", variant


@pytest.mark.parametrize("variant", ["nostate", "empty", "truncated", "array", "no_state_key"])
def test_damaged_state_json_dry_run(tmp_path: Path, monkeypatch, capsys, variant: str) -> None:
    hub = make_hub(tmp_path)
    worktree_mod.prepare(hub=hub, bead="b1")
    d = hub / ".helios" / "runs" / "b1" / "attempt-1"
    d.mkdir(parents=True)
    if variant == "empty":
        (d / "state.json").write_text("")
    elif variant == "truncated":
        (d / "state.json").write_text('{"state": "laun')
    elif variant == "array":
        (d / "state.json").write_text("[]")
    elif variant == "no_state_key":
        (d / "state.json").write_text('{"pid": 1}')
    before = {str(p.relative_to(hub)) for p in hub.rglob("*")}
    set_fake(monkeypatch, write_script(tmp_path, {"report": {"status": "done", "summary": "ok"}}))
    beads = beads_mod.FakeBeads([make_bead("b1")])
    rc = run_mod.run_many(["b1"], hub=hub, beads=beads,
                          config=config_mod.load(hub), harness_override="fake",
                          dry_run=True)
    out = capsys.readouterr().out
    after = {str(p.relative_to(hub)) for p in hub.rglob("*")}
    assert rc == 0 and "recovery:" in out, variant
    assert before == after and beads.argv_log == [], variant


def test_closed_bead_is_preflight_error(tmp_path: Path, monkeypatch, capsys) -> None:
    hub = make_hub(tmp_path)
    set_fake(monkeypatch, write_script(
        tmp_path, {"report": {"status": "done", "summary": "ok"}}))
    closed = make_bead("b1")
    closed.status = "closed"
    beads = beads_mod.FakeBeads([closed])
    assert run_mod.run_many(["b1"], hub=hub, beads=beads,
                            config=config_mod.load(hub),
                            harness_override="fake") == 2
    assert not (hub / ".helios" / "runs").exists()
    assert not (hub / ".claude" / "worktrees").exists()
    beads2 = beads_mod.FakeBeads([closed])
    assert run_mod.run_one("b1", hub=hub, beads=beads2,
                           config=config_mod.load(hub),
                           harness_override="fake", dry_run=True) == 2


def test_handler_takes_no_lock() -> None:
    import signal as _signal

    prev = _signal.getsignal(_signal.SIGINT)
    _signal.signal(_signal.SIGINT, run_mod._handle_sigint)
    try:
        with run_mod._RUNNING_LOCK:
            with run_mod._RUN_DEPTH_LOCK:
                os.kill(os.getpid(), _signal.SIGINT)
        assert run_mod._INTERRUPT.is_set()
    finally:
        _signal.signal(_signal.SIGINT, prev)
        run_mod._INTERRUPT.clear()


def test_install_handler_only_main_thread() -> None:
    box: dict = {}
    thread = __import__("threading").Thread(
        target=lambda: box.update(ok=run_mod._install_handler()))
    thread.start()
    thread.join()
    assert box["ok"] is False


def test_sigint_storm_launch(tmp_path: Path) -> None:
    import threading as _threading

    hub = make_hub(tmp_path)
    script = write_script(
        tmp_path, {"sleep_s": 20, "stdout": "slow", "session_id": "s1",
                   "report": {"status": "done", "summary": "ok"}})
    proc = spawn_child(hub, script, "b1,b2,b3", maxp=3)
    for bid in ("b1", "b2", "b3"):
        wait_for(hub / ".helios" / "runs" / bid / "attempt-1" / "stdout.jsonl", "slow")
    def storm() -> None:
        for _ in range(10):
            try:
                os.kill(proc.pid, signal.SIGINT)
            except ProcessLookupError:
                return
            time.sleep(0.005)
    worker = _threading.Thread(target=storm)
    worker.start()
    out, _ = proc.communicate(timeout=90)
    worker.join()
    assert proc.returncode == 4
    for bid in ("b1", "b2", "b3"):
        a1 = hub / ".helios" / "runs" / bid / "attempt-1"
        assert attempt_mod.read_state(a1)["state"] == "finalized"
        assert read_envelope(hub, bid, 1)["execution_status"] == "interrupted"
        assert not attempt_mod.is_pid_alive(attempt_mod.read_state(a1).get("pid"))
    assert run_mod._RUN_DEPTH == 0
    assert not [t for t in _threading.enumerate()
                if "_stopper_main" in t.name and t.is_alive()]
    _ = out


def test_session_survives_crash_before_envelope(
    tmp_path: Path, monkeypatch
) -> None:
    import helios.ownership as _own

    hub = make_hub(tmp_path)
    set_fake(monkeypatch, write_script(
        tmp_path, {"report": {"status": "done", "summary": "ok"}, "session_id": "s7"}))
    real = _own.check

    def boom(*a, **k):
        raise RuntimeError("check died")

    monkeypatch.setattr(_own, "check", boom)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    with pytest.raises(RuntimeError):
        run_mod.run_one("b1", hub=hub, beads=beads,
                        config=config_mod.load(hub), harness_override="fake")
    monkeypatch.setattr(_own, "check", real)
    assert attempt_mod.read_state(
        hub / ".helios" / "runs" / "b1" / "attempt-1")["session_id"] == "s7"
    rc = run_mod.run_one("b1", hub=hub, beads=beads,
                         config=config_mod.load(hub), harness_override="fake")
    assert rc == 0
    assert read_envelope(hub, "b1", 1)["session_id"] == "s7"
    assert beads.beads["b1"].metadata["session"] == "fake:s7"


def test_memory_lookup_gates_preflight(tmp_path: Path, monkeypatch) -> None:
    from helios import memory as memory_mod
    from helios.commands import run as run_cmd

    hub = make_hub(tmp_path)
    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "stdout": "x", "session_id": "s1",
                   "report": {"status": "done", "summary": "s"}}))
    beads = beads_mod.FakeBeads([make_bead("b1", memories=["m1"])])
    cfg = config_mod.load(hub)
    assert run_mod.run_many(["b1"], hub=hub, beads=beads, config=cfg,
                            harness_override="fake") == 2
    beads2 = beads_mod.FakeBeads([make_bead("b1", memories=["m1"])])
    # A memory lookup failure never falls back to "" in the prompt (round-1-fix
    # item 2), so this second run, whose injected memory_has is decoupled from
    # the backend, needs the memory to actually be there once preflight passes.
    beads2.remember(
        "m1", memory_mod.serialize({"source": "hel-1#1", "status": "active"}, "m1 body")
    )
    assert run_mod.run_many(["b1"], hub=hub, beads=beads2, config=cfg,
                            harness_override="fake",
                            memory_has=lambda key: key == "m1") == 0
    assert "b1" in beads2.closed


def test_memory_has_for_backends(tmp_path: Path) -> None:
    from helios.commands import run as run_cmd

    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    beads = beads_mod.FakeBeads([])
    assert run_cmd.memory_has_for(beads, cfg)("anything") is False
    beads.remember("m1", "v")
    assert run_cmd.memory_has_for(beads, cfg)("m1") is True
    assert run_cmd.memory_has_for(beads, cfg)("nope") is False


def test_memory_key_rule_rejected_both_backends(tmp_path: Path) -> None:
    """Bad keys and ``schema_version`` never exist, before any lookup (SPEC §13)."""
    from helios import config as C
    from helios.commands import run as run_cmd

    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([])
    beads.remember("schema_version", "v")
    beads_has = run_cmd.memory_has_for(beads, config_mod.load(hub))
    for bad in ("M1", "../../README", "schema_version", "has space", ""):
        assert beads_has(bad) is False, bad

    export = hub / ".helios" / "memories"
    export.mkdir(parents=True)
    (export / "schema_version.md").write_text(
        "helios-memory 1\n{\"source\": \"x#1\", \"status\": \"active\"}\n\nbody"
    )
    files_cfg = C.Config(hub=hub, memory=C.MemoryConfig(backend="files"))
    files_has = run_cmd.memory_has_for(beads_mod.FakeBeads([]), files_cfg)
    for bad in ("M1", "../../README", "schema_version", "has space", ""):
        assert files_has(bad) is False, bad


def test_memory_lookup_failure_is_distinct_preflight_error(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A failing ``bd recall`` is a preflight error distinct from "does not exist"."""
    from helios.commands import run as run_cmd

    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1", memories=["m1"])])

    def boom(key: str) -> str | None:
        raise RuntimeError("bd exploded")

    monkeypatch.setattr(beads, "recall", boom)
    cfg = config_mod.load(hub)
    rc = run_mod.run_many(["b1"], hub=hub, beads=beads, config=cfg,
                          harness_override="fake",
                          memory_has=run_cmd.memory_has_for(beads, cfg))
    err = capsys.readouterr().err
    assert rc == 2
    assert "helios: memory lookup failed for m1: bd exploded" in err
    assert "does not exist" not in err
    assert not (hub / ".helios" / "runs").exists()


def test_lock_refusal_second_process(tmp_path: Path) -> None:
    hub = make_hub(tmp_path)
    script = write_script(
        tmp_path,
        {"sleep_s": 3, "stdout": "slow", "report": {"status": "done", "summary": "ok"}},
        name="s1.json")
    proc = spawn_child(hub, script, "b1", slow=4)
    try:
        wait_for(hub / ".helios" / "runs" / "b1" / "attempt-1" / "state.log",
                 "allocated")
        quick = spawn_child(
            hub, write_script(tmp_path, {"sleep_s": 1,
                                         "report": {"status": "done", "summary": "ok"}},
                              name="s2.json"), "b1")
        out2, err2 = quick.communicate(timeout=60)
        assert quick.returncode == 2
        assert "helios attach b1" in out2 + err2
        out1, _ = proc.communicate(timeout=60)
        assert proc.returncode == 0
        assert not (hub / ".helios" / "runs" / "b1" / "attempt-2").exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


DEADLOCK_CHILD = """\
import os, signal, sys, threading, time
from pathlib import Path
from helios import beads as B, config as C, run as R

hub = Path(os.environ["HELIOS_CHILD_HUB"])
beads = B.FakeBeads([B.Bead(id="b1", kind="impl", files=["src/"], test="true")])
cfg = C.load(hub)
prev = signal.getsignal(signal.SIGINT)

ev = R._INTERRUPT
orig_notify = ev._cond.notify_all
shot = {"n": 0}

def notify_all():
    # Fires the first time _INTERRUPT.set() runs after the outermost run's
    # cleanup begins: exactly the site of the round 6 reentrancy deadlock.
    if shot["n"] == 0 and not R._RUN_ACTIVE.is_set():
        shot["n"] = 1
        os.kill(os.getpid(), signal.SIGINT)
        time.sleep(0.02)
    return orig_notify()

ev._cond.notify_all = notify_all
rc = R.run_many(["b1"], hub=hub, beads=beads, config=cfg, harness_override="fake")
ok = (rc == 0 and shot["n"] == 1
      and signal.getsignal(signal.SIGINT) is prev and R._RUN_DEPTH == 0)
print(f"RESULT rc={rc} shot={shot['n']} ok={ok}", flush=True)
sys.exit(0 if ok else 1)
"""


def test_sigint_reentry_in_cleanup_does_not_deadlock(tmp_path: Path) -> None:
    """A SIGINT racing _INTERRUPT.set() in run_many's own cleanup must not hang (SPEC §7.1)."""
    hub = make_hub(tmp_path)
    script = write_script(tmp_path, {"report": {"status": "done", "summary": "ok"}})
    env = dict(os.environ, HELIOS_FAKE_SCRIPT=str(script), HELIOS_CHILD_HUB=str(hub))
    proc = subprocess.run(
        [sys.executable, "-c", DEADLOCK_CHILD], env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


STORM_EMPTY_CHILD = """\
import os, signal, sys, threading, time
from pathlib import Path
from helios import beads as B, config as C, run as R

hub = Path(os.environ["HELIOS_CHILD_HUB"])
cfg = C.load(hub)
beads = B.FakeBeads([])
# Ignore SIGINT outside run_many so the storm thread never hits this
# script's own default handler between calls; run_many installs and
# restores its own handler around each call.
signal.signal(signal.SIGINT, signal.SIG_IGN)
prev = signal.getsignal(signal.SIGINT)

stop = threading.Event()
sent = [0]

def storm():
    while not stop.is_set():
        try:
            os.kill(os.getpid(), signal.SIGINT)
        except ProcessLookupError:
            return
        sent[0] += 1
        time.sleep(0.0005)

t = threading.Thread(target=storm, daemon=True)
t.start()
end = time.monotonic() + 4
n = 0
try:
    while time.monotonic() < end:
        R.run_many([], hub=hub, beads=beads, config=cfg)
        n += 1
finally:
    stop.set()
    t.join()

ok = (sent[0] >= 2000 and signal.getsignal(signal.SIGINT) is prev and R._RUN_DEPTH == 0)
print(f"RESULT n={n} sent={sent[0]} ok={ok}", flush=True)
sys.exit(0 if ok else 1)
"""


def test_sigint_storm_on_empty_run_many_no_exception(tmp_path: Path) -> None:
    """A SIGINT storm on ``run_many([])`` never raises; the handler is always restored."""
    hub = make_hub(tmp_path)
    env = dict(os.environ, HELIOS_CHILD_HUB=str(hub))
    proc = subprocess.run(
        [sys.executable, "-c", STORM_EMPTY_CHILD], env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
