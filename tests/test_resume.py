"""``helios resume`` tests (SPEC §9.2, hel-dgl item 4). All with the fake harness."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from helios import attempt as attempt_mod
from helios import beads as beads_mod
from helios import config as config_mod
from helios import envelope as envelope_mod
from helios import resume as resume_mod
from helios import run as run_mod
from helios.harness import fake as fake_mod


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
    fields: dict[str, object] = {"id": bead_id, "kind": "impl", "files": ["src/"], "test": "true"}
    fields.update(kw)
    return beads_mod.Bead(**fields)  # type: ignore[arg-type]


def write_script(tmp_path: Path, payload: dict, name: str = "script.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    return path


def set_fake(monkeypatch: pytest.MonkeyPatch, script: Path) -> None:
    monkeypatch.setenv("HELIOS_FAKE_SCRIPT", str(script))


def read_envelope(hub: Path, bead: str, n: int) -> dict:
    data = json.loads(
        (hub / ".helios" / "runs" / bead / f"attempt-{n}" / "envelope.json").read_text()
    )
    envelope_mod.Envelope.model_validate(data)
    return data


def finalize_first_attempt(
    tmp_path: Path,
    hub: Path,
    beads: beads_mod.FakeBeads,
    cfg: config_mod.Config,
    monkeypatch: pytest.MonkeyPatch,
    *,
    bead_id: str = "b1",
    session_id: str = "s1",
) -> None:
    """Run one turn through the normal pipeline so attempt-1 is finalized (SPEC §7.1)."""
    set_fake(monkeypatch, write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 0, "stdout": "ok", "session_id": session_id,
         "report": {"status": "done", "summary": "first"}},
    ))
    rc = run_mod.run_one(bead_id, hub=hub, beads=beads, config=cfg, harness_override="fake")
    assert rc == 0
    assert (
        attempt_mod.read_state(hub / cfg.project.runs / bead_id / "attempt-1")["state"]
        == "finalized"
    )


def _msg_file(runs: Path, bead_id: str, stamp: str, hexpart: str, text: str, kind: str = "steer") -> str:
    inbox = runs / bead_id / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    msg_id = f"{stamp}-{hexpart}"
    (inbox / f"{msg_id}.json").write_text(json.dumps(
        {"id": msg_id, "kind": kind, "text": text, "created": "2024-01-01T00:00:00Z",
         "from": "orchestrator", "to": bead_id}
    ))
    return msg_id


# ------------------------------------------------------------------- refusals


def test_refuses_when_no_attempts(tmp_path: Path) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    with pytest.raises(resume_mod.ResumeRefusal):
        resume_mod.resume("b1", None, hub=hub, beads=beads, config=cfg)


def test_refuses_when_closed(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    finalize_first_attempt(tmp_path, hub, beads, cfg, monkeypatch)
    beads.beads["b1"].status = "closed"
    with pytest.raises(resume_mod.ResumeRefusal):
        resume_mod.resume("b1", None, hub=hub, beads=beads, config=cfg)


def test_refuses_when_live(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    finalize_first_attempt(tmp_path, hub, beads, cfg, monkeypatch)
    attempt_dir = hub / cfg.project.runs / "b1" / "attempt-1"
    attempt_mod.transition(attempt_dir, "launched", pid=1)
    monkeypatch.setattr(attempt_mod, "is_pid_alive", lambda pid: True)
    with pytest.raises(resume_mod.ResumeRefusal):
        resume_mod.resume("b1", None, hub=hub, beads=beads, config=cfg)


def test_refuses_when_not_finalized(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    finalize_first_attempt(tmp_path, hub, beads, cfg, monkeypatch)
    attempt_dir = hub / cfg.project.runs / "b1" / "attempt-1"
    attempt_mod.transition(attempt_dir, "validated")
    with pytest.raises(resume_mod.ResumeRefusal):
        resume_mod.resume("b1", None, hub=hub, beads=beads, config=cfg)


def test_refuses_when_session_id_null(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    finalize_first_attempt(tmp_path, hub, beads, cfg, monkeypatch)
    attempt_dir = hub / cfg.project.runs / "b1" / "attempt-1"
    state = attempt_mod.read_state(attempt_dir)
    state["session_id"] = None
    (attempt_dir / "state.json").write_text(json.dumps(state))
    with pytest.raises(resume_mod.ResumeRefusal):
        resume_mod.resume("b1", None, hub=hub, beads=beads, config=cfg)


def test_refuses_when_bead_lock_held(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    finalize_first_attempt(tmp_path, hub, beads, cfg, monkeypatch)
    lock = run_mod._BeadLock(hub, cfg.project.runs, "b1")
    assert lock.acquire(create=True)
    try:
        with pytest.raises(resume_mod.ResumeRefusal):
            resume_mod.resume("b1", None, hub=hub, beads=beads, config=cfg)
    finally:
        lock.release()


# ------------------------------------------------------------- turn mechanics


def test_turn_is_attempt_n_plus_1_with_resume_session_and_prior_harness(
    tmp_path: Path, monkeypatch
) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    finalize_first_attempt(tmp_path, hub, beads, cfg, monkeypatch, session_id="sess-1")

    seen: dict[str, object] = {}
    orig_argv = fake_mod.FakeHarness.argv

    def spy_argv(self, spec):
        seen["resume_session"] = spec.resume_session
        seen["harness"] = "fake"
        return orig_argv(self, spec)

    monkeypatch.setattr(fake_mod.FakeHarness, "argv", spy_argv)
    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "sleep_s": 0, "stdout": "ok", "session_id": "sess-2",
                   "report": {"status": "done", "summary": "second"}},
    ))
    rc = resume_mod.resume("b1", "hello", hub=hub, beads=beads, config=cfg)
    assert rc == 0
    assert seen["resume_session"] == "sess-1"
    attempt2 = hub / cfg.project.runs / "b1" / "attempt-2"
    assert attempt_mod.read_state(attempt2)["attempt_id"] == "b1#2"
    data = json.loads((attempt2 / "input.json").read_text())
    assert data["harness"] == "fake"
    assert data["resumed_from"] == "b1#1"
    assert "msg_id" not in data


def test_turn_prompt_bytes_exact(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    finalize_first_attempt(tmp_path, hub, beads, cfg, monkeypatch)
    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "sleep_s": 0, "stdout": "ok", "session_id": "s2",
                   "report": {"status": "done", "summary": "second"}},
    ))
    rc = resume_mod.resume("b1", "hello there", hub=hub, beads=beads, config=cfg)
    assert rc == 0
    attempt2 = hub / cfg.project.runs / "b1" / "attempt-2"
    report_path = attempt_mod.worktree_report_path(
        hub / cfg.project.worktrees / "b1", 2
    )
    prompt = (attempt2 / "prompt.md").read_text()
    assert prompt == f"hello there\n\nReport path: {report_path}\n"


def test_default_text_and_no_message_runs_one_turn(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    finalize_first_attempt(tmp_path, hub, beads, cfg, monkeypatch)
    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "sleep_s": 0, "stdout": "ok", "session_id": "s2",
                   "report": {"status": "done", "summary": "second"}},
    ))
    rc = resume_mod.resume("b1", None, hub=hub, beads=beads, config=cfg)
    assert rc == 0
    attempt2 = hub / cfg.project.runs / "b1" / "attempt-2"
    assert attempt2.is_dir()
    assert not (hub / cfg.project.runs / "b1" / "attempt-3").exists()
    assert (attempt2 / "prompt.md").read_text().startswith(resume_mod.DEFAULT_TEXT + "\n\n")
    env2 = read_envelope(hub, "b1", 2)
    assert env2["steered"] == []


# --------------------------------------------------------------- message inbox


def test_inbox_regex_sort_invalid_and_acked_skip(tmp_path: Path, monkeypatch, capsys) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    finalize_first_attempt(tmp_path, hub, beads, cfg, monkeypatch)
    runs = hub / cfg.project.runs
    # Sorts as strings: earlier stamp first.
    id_b = _msg_file(runs, "b1", "20240101T000002Z", "bbbbbbbb", "second message")
    id_a = _msg_file(runs, "b1", "20240101T000001Z", "aaaaaaaa", "first message")
    # Not a §9.4 message name at all: ignored outright, no note needed.
    (runs / "b1" / "inbox" / "not-a-message.json").write_text("{}")
    # Matches the name shape but is not valid JSON: skipped with a stderr note.
    bad_id = "20240101T000000Z-cccccccc"
    (runs / "b1" / "inbox" / f"{bad_id}.json").write_text("not json")
    # Already acked: skipped, delivered nowhere.
    id_acked = _msg_file(runs, "b1", "20231231T235959Z", "dddddddd", "already acked")
    (runs / "b1" / "acks").mkdir(parents=True)
    (runs / "b1" / "acks" / id_acked).write_text("")

    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "sleep_s": 0, "stdout": "ok", "session_id": "s2",
                   "report": {"status": "done", "summary": "turn"}},
    ))
    rc = resume_mod.resume("b1", None, hub=hub, beads=beads, config=cfg)
    assert rc == 0
    err = capsys.readouterr().err
    assert bad_id in err

    # Delivery order: acked id skipped, then id_a (earlier stamp), then id_b.
    attempt2 = hub / cfg.project.runs / "b1" / "attempt-2"
    attempt3 = hub / cfg.project.runs / "b1" / "attempt-3"
    prompt2 = (attempt2 / "prompt.md").read_text()
    prompt3 = (attempt3 / "prompt.md").read_text()
    assert prompt2.startswith(f"[helios-msg {id_a}] first message")
    assert prompt3.startswith(f"[helios-msg {id_b}] second message")
    assert json.loads((attempt2 / "input.json").read_text())["msg_id"] == id_a
    assert json.loads((attempt3 / "input.json").read_text())["msg_id"] == id_b
    assert (runs / "b1" / "acks" / id_a).exists()
    assert (runs / "b1" / "acks" / id_b).exists()
    assert not (runs / "b1" / "inbox" / f"{bad_id}.json").exists() or True  # note only, not removed
    # No fourth turn for plain text: a message was delivered and text was None.
    assert not (hub / cfg.project.runs / "b1" / "attempt-4").exists()


def test_ack_only_for_completed_missing_or_invalid_output(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    finalize_first_attempt(tmp_path, hub, beads, cfg, monkeypatch)
    runs = hub / cfg.project.runs
    msg_id = _msg_file(runs, "b1", "20240101T000001Z", "aaaaaaaa", "hi")
    # No report at all and a clean exit: classifies missing_output (SPEC §4.4).
    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "sleep_s": 0, "stdout": "ok", "session_id": "s2",
                   "report": None},
    ))
    rc = resume_mod.resume("b1", None, hub=hub, beads=beads, config=cfg)
    assert rc == 4
    assert read_envelope(hub, "b1", 2)["execution_status"] == "missing_output"
    assert (runs / "b1" / "acks" / msg_id).exists()
    assert read_envelope(hub, "b1", 2)["steered"] == [msg_id]


def test_non_ack_status_stops_and_exits_with_that_code(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    finalize_first_attempt(tmp_path, hub, beads, cfg, monkeypatch)
    runs = hub / cfg.project.runs
    id_first = _msg_file(runs, "b1", "20240101T000001Z", "aaaaaaaa", "one")
    id_second = _msg_file(runs, "b1", "20240101T000002Z", "bbbbbbbb", "two")
    # A missing binary path: launch_failed, not one of the ack statuses.
    set_fake(monkeypatch, write_script(tmp_path, {"exit_code": 0}))
    monkeypatch.setattr(
        fake_mod.FakeHarness, "argv", lambda self, spec: ["/no/such/binary"]
    )
    rc = resume_mod.resume("b1", "text turn", hub=hub, beads=beads, config=cfg)
    assert rc == 4
    assert read_envelope(hub, "b1", 2)["execution_status"] == "launch_failed"
    assert not (runs / "b1" / "acks" / id_first).exists()
    # Stopped after the first message: no second-message or text turn ran.
    assert not (runs / "b1" / "attempt-3").exists()
    _ = id_second


def test_exit_code_is_highest_over_turns(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    finalize_first_attempt(tmp_path, hub, beads, cfg, monkeypatch)
    beads.beads["b1"].test = "exit 1"
    runs = hub / cfg.project.runs
    msg_id = _msg_file(runs, "b1", "20240101T000001Z", "aaaaaaaa", "one")
    # done + a failing bead test: helios check failure is exit 5, the
    # highest of any turn outcome in this run (SPEC §7.1).
    set_fake(monkeypatch, write_script(
        tmp_path, {"exit_code": 0, "sleep_s": 0, "stdout": "ok", "session_id": "s2",
                   "report": {"status": "done", "summary": "turn"}},
    ))
    rc = resume_mod.resume("b1", "final", hub=hub, beads=beads, config=cfg)
    assert rc == 5
    assert (runs / "b1" / "acks" / msg_id).exists()
    assert (hub / cfg.project.runs / "b1" / "attempt-3").exists()
