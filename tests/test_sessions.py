"""Session and message tests (SPEC §9.2, §9.4)."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from helios import attempt, cli, events, messages, sessions
from helios.beads import Bead, FakeBeads
from helios.commands import ps as ps_command


def _attempt(root: Path, bead: str = "b1", n: int = 1, **state) -> Path:
    directory = root / ".helios" / "runs" / bead / f"attempt-{n}"
    directory.mkdir(parents=True, exist_ok=True)
    updated = state.pop("updated", None)
    values = {"attempt_id": f"{bead}#{n}", "state": "allocated", **state}
    attempt.write_state(directory, **values)
    if updated is not None:
        record = json.loads((directory / "state.json").read_text())
        record["updated"] = updated
        (directory / "state.json").write_text(json.dumps(record))
    return directory


def _input(directory: Path, **values) -> None:
    (directory / "input.json").write_text(json.dumps(values))


def test_message_fields_regex_atomic_comment_and_event(tmp_path: Path) -> None:
    directory = _attempt(tmp_path)
    store = FakeBeads([Bead(id="b1")])
    msg_id, queued = messages.say(tmp_path, ".helios/runs", "b1", "hello", bead_store=store)
    assert not queued
    assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", msg_id)
    payload = json.loads((directory.parent / "inbox" / f"{msg_id}.json").read_text())
    assert payload["id"] == msg_id
    assert payload["kind"] == "steer"
    assert payload["text"] == "hello"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", payload["created"])
    assert payload["from"] == "orchestrator" and payload["to"] == "b1"
    assert not list((directory.parent / "inbox").glob("message.*.tmp"))
    messages.add_comment(store, "b1", "steer", msg_id, "hello")
    assert len(store.comments("b1")) == 1
    event = json.loads((tmp_path / ".helios" / "events.jsonl").read_text())
    assert event["type"] == "steer" and event["source"] == "orchestrator"
    assert event["detail"] == msg_id


def test_message_answer_and_live_attempt_is_queued(tmp_path: Path) -> None:
    directory = _attempt(tmp_path, state="launched", pid=123)
    store = FakeBeads([Bead(id="b1")])
    msg_id, queued = messages.say(tmp_path, ".helios/runs", "b1", "answer", kind="answer",
                                  bead_store=store, pid_alive=lambda pid: True)
    assert queued
    assert json.loads((directory.parent / "inbox" / f"{msg_id}.json").read_text())["kind"] == "answer"


def test_message_without_runs_fails_without_writing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no runs directory for b1"):
        messages.say(tmp_path, ".helios/runs", "b1", "hello", bead_store=FakeBeads())
    assert not (tmp_path / ".helios").exists()


def test_say_cli_unexecutable_bd_is_failed_comment_exit_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _attempt(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_bd = bin_dir / "bd"
    fake_bd.write_text("#!/bin/sh\necho hi\n")
    fake_bd.chmod(0o644)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["say", "b1", "hi"]) == 1
    assert capsys.readouterr().err == "helios: [Errno 13] Permission denied: 'bd'\n"
    inbox = list((tmp_path / ".helios" / "runs" / "b1" / "inbox").glob("*.json"))
    assert len(inbox) == 1
    assert not (tmp_path / ".helios" / "events.jsonl").exists()


def test_ps_rows_sort_filter_dead_state_events_and_unknowns(tmp_path: Path) -> None:
    first = _attempt(tmp_path, "b2", state="finalized", updated=None)
    _input(first, harness="fake", worktree="/wt")
    _attempt(tmp_path, "b1", state="launched", pid=999999, updated=None)
    _input(tmp_path / ".helios" / "runs" / "b1" / "attempt-1", harness="fake", worktree="/wt")
    progress = _attempt(tmp_path, "b3", state="finalized", updated=None)
    _input(progress)
    store = FakeBeads([Bead(id="b2"), Bead(id="b3", status="in_progress")])
    events_path = tmp_path / ".helios" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("not json\n")
    events.append(tmp_path, source="helios", type="launched", bead="other", attempt="other#1")
    events.append(tmp_path, source="helios", type="failed", bead="b1", attempt="b1#1")
    events.append(tmp_path, source="helios", type="completed", bead="b1", attempt="b1#1")
    rows = sessions.rows(tmp_path, ".helios/runs", bead_store=store)
    assert [row["bead"] for row in rows] == ["b1", "b3"]
    assert rows[0]["state"] == "launched"
    assert rows[0]["last_event"] == "completed"
    assert rows[0]["alive"] is False
    assert rows[1]["harness"] is None and rows[1]["worktree"] is None


def test_ps_bd_failure_uses_unknown_fields_and_damaged_state_is_allocated(tmp_path: Path) -> None:
    directory = _attempt(tmp_path, "b1")
    (directory / "state.json").write_text("")

    class Broken(FakeBeads):
        def show(self, bead_id):
            raise RuntimeError("bd failed")

    row = sessions.rows(tmp_path, ".helios/runs", bead_store=Broken())[0]
    assert row["state"] == "allocated"
    assert row["unit"] is None and row["kind"] is None


@pytest.mark.parametrize("line", [b"[1]\n", b'"a string"\n', b"42\n", b"null\n", b"\xff\n"])
def test_ps_skips_bad_event_lines(tmp_path: Path, line: bytes) -> None:
    directory = _attempt(tmp_path)
    (tmp_path / ".helios" / "events.jsonl").write_bytes(line)
    rows = sessions.rows(tmp_path, ".helios/runs", bead_store=FakeBeads([Bead(id="b1")]))
    assert rows[0]["last_event"] is None


def test_ps_rejects_non_string_harness_and_event_type(tmp_path: Path) -> None:
    directory = _attempt(tmp_path)
    (directory / "input.json").write_text('{"harness": ["x"]}')
    events_path = tmp_path / ".helios" / "events.jsonl"
    events_path.write_text('{"bead":"b1","attempt":"b1#1","type":5}\n')
    row = sessions.rows(tmp_path, ".helios/runs", bead_store=FakeBeads([Bead(id="b1")]))[0]
    assert row["harness"] is None and row["session"] is None and row["last_event"] is None


def test_ps_json_is_strict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    directory = _attempt(tmp_path)
    (directory / "input.json").write_text('{"harness": NaN}')
    monkeypatch.chdir(tmp_path)
    assert cli.main(["ps", "--json"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def test_ps_text_shows_dash_for_non_string_harness_and_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    directory = _attempt(tmp_path)
    (directory / "input.json").write_text('{"harness": ["x"]}')
    monkeypatch.chdir(tmp_path)
    assert cli.main(["ps"]) == 0
    header, row = capsys.readouterr().out.splitlines()
    columns = dict(zip(header.split("\t"), row.split("\t")))
    assert columns["harness"] == "-" and columns["session"] == "-"


def test_ps_naive_and_future_age_are_safe_and_json_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    directory = _attempt(tmp_path, state="launched", pid=None, updated="2026-01-01T00:00:00")
    rows = sessions.rows(tmp_path, ".helios/runs", bead_store=FakeBeads([Bead(id="b1")]), now=now)
    assert rows[0]["age"] is None and rows[0]["state"] == "launched"
    monkeypatch.chdir(tmp_path)
    assert cli.main(["ps"]) == 0
    assert "launched (dead)" in capsys.readouterr().out
    directory = _attempt(tmp_path, n=2, updated="2027-01-01T00:00:00Z")
    rows = sessions.rows(tmp_path, ".helios/runs", bead_store=FakeBeads([Bead(id="b1")]), now=now)
    assert rows[0]["age"] == "0s"


@pytest.mark.parametrize("pid", [0, -1, True, "123", 1.5])
def test_stop_rejects_invalid_pid_without_signal_or_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pid) -> None:
    directory = _attempt(tmp_path, state="launched", pid=pid)
    monkeypatch.setattr(sessions.os, "killpg", lambda *args: pytest.fail("killpg called"))
    with pytest.raises(ValueError, match="no running attempt for b1"):
        sessions.stop(tmp_path, ".helios/runs", "b1")
    assert not (directory / "stop-requested").exists()


def test_stop_ignores_permission_error_from_gone_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = _attempt(tmp_path, state="launched", pid=123)
    monkeypatch.setattr(sessions.attempt, "is_pid_alive", lambda pid: True)
    def gone(pid, sig):
        raise PermissionError("gone")
    monkeypatch.setattr(sessions.os, "killpg", gone)
    sessions.stop(tmp_path, ".helios/runs", "b1")
    assert (directory / "stop-requested").is_file()


def test_commands_prefix_configuration_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "workflow.toml").write_text("[bogus]\nx = 1\n")
    monkeypatch.chdir(tmp_path)
    for args in (["ps"], ["attach", "b1"], ["say", "b1", "x"], ["stop", "b1"]):
        assert cli.main(args) == 2
        assert capsys.readouterr().err.startswith("helios: ")


def test_commands_prefix_config_recursion_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    (tmp_path / ".agents").mkdir()
    nested = "x = " + "[" * 5000 + "]" * 5000 + "\n"
    (tmp_path / ".agents" / "workflow.toml").write_text(nested)
    monkeypatch.chdir(tmp_path)
    for args in (["ps"], ["attach", "b1"], ["say", "b1", "x"], ["stop", "b1"]):
        assert cli.main(args) == 2
        err = capsys.readouterr().err
        assert err.startswith("helios: ") and "Traceback" not in err


@pytest.mark.parametrize("seconds, expected", [(59, "59s"), (60, "1m"), (3599, "59m"),
                                                  (3600, "1h"), (86399, "23h"), (86400, "1d")])
def test_ps_age_boundaries(tmp_path: Path, seconds: int, expected: str) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    directory = _attempt(tmp_path, updated=(now - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z"))
    _input(directory, harness="fake", worktree="/wt")
    assert sessions.rows(tmp_path, ".helios/runs", bead_store=FakeBeads([Bead(id="b1")]), now=now)[0]["age"] == expected


def test_ps_text_and_json_empty_and_exact_columns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["ps"]) == 0
    assert capsys.readouterr().out == "bead\tunit\tkind\tharness\tstate\tattempt\tage\tworktree\tsession\n"
    assert cli.main(["ps", "--json"]) == 0
    assert capsys.readouterr().out == "[]\n"
    values = [{"bead": "b1", "unit": None, "kind": "impl", "harness": None,
               "state": "allocated", "attempt": "b1#1", "age": "-", "worktree": None,
               "session": None, "alive": False, "last_event": None}]
    monkeypatch.setattr(ps_command.sessions, "rows", lambda *args, **kwargs: values)
    assert ps_command.run(SimpleNamespace(as_json=False)) == 0
    assert capsys.readouterr().out == "bead\tunit\tkind\tharness\tstate\tattempt\tage\tworktree\tsession\n" \
        "b1\t-\timpl\t-\tallocated\tb1#1\t-\t-\t-\n"


def test_attach_error_cases_and_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from helios import config

    with pytest.raises(ValueError, match="no attempt for b1"):
        sessions.attach(tmp_path, ".helios/runs", "b1", harness_lookup=lambda name: None)
    directory = _attempt(tmp_path)
    with pytest.raises(ValueError, match="no harness recorded for b1#1"):
        sessions.attach(tmp_path, ".helios/runs", "b1", harness_lookup=lambda name: None)
    _input(directory, harness="fake", worktree=str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="worktree missing for b1#1"):
        sessions.attach(tmp_path, ".helios/runs", "b1", harness_lookup=lambda name: None)
    worktree = tmp_path / ".claude" / "worktrees" / "b1"
    worktree.mkdir(parents=True)
    _input(directory, harness="fake", worktree=str(worktree))
    with pytest.raises(ValueError, match="no session recorded for b1#1"):
        sessions.attach(tmp_path, ".helios/runs", "b1", harness_lookup=lambda name: None)
    attempt.transition(directory, "launched", pid=None, session_id="s1")
    calls = []
    class Harness:
        def attach_command(self, session_id, spec):
            calls.append((session_id, spec))
            return ["fake", "--session", session_id]
    monkeypatch.setattr(config, "load", lambda hub: config.Config(hub=hub, harness={"fake": config.HarnessConfig(binary="configured-fake")}))
    executed = []
    sessions.attach(tmp_path, ".helios/runs", "b1", harness_lookup=lambda name: Harness(),
                    execvp=lambda file, argv: executed.append((file, argv)))
    assert executed == [("configured-fake", ["configured-fake", "--session", "s1"])]
    assert calls[0][0] == "s1" and calls[0][1].prompt == ""


def test_stop_real_group_and_gone_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    process = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        directory = _attempt(tmp_path, state="launched", pid=process.pid)
        before = (directory / "state.json").read_bytes()
        sessions.stop(tmp_path, ".helios/runs", "b1")
        process.wait(timeout=5)
        assert process.returncode != 0
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\n", (directory / "stop-requested").read_text())
        assert (directory / "state.json").read_bytes() == before
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    gone = _attempt(tmp_path, "b2", state="launched", pid=123)
    monkeypatch.setattr(sessions.attempt, "is_pid_alive", lambda pid: True)
    monkeypatch.setattr(sessions.os, "killpg", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))
    sessions.stop(tmp_path, ".helios/runs", "b2")


def test_cli_dispatches_all_four_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["ps"]) == 0
    assert cli.main(["attach", "b1"]) == 2
    assert cli.main(["say", "b1", "hello"]) == 2
    assert cli.main(["stop", "b1"]) == 2
    assert "no attempt for b1" in capsys.readouterr().err
