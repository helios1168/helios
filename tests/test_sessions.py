"""Session and message tests (SPEC §9.2, §9.4)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
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


def test_state_json_overflow_number_reads_as_allocated(tmp_path: Path) -> None:
    directory = _attempt(tmp_path, state="launched", pid=123, session_id="s1")
    good = json.loads((directory / "state.json").read_text())
    corrupted = json.dumps(good)[:-1] + ', "huge": 1e999}'
    (directory / "state.json").write_text(corrupted)
    state = attempt.read_state(directory)
    assert state["state"] == "allocated"
    assert sessions.safe_state(directory)["state"] == "allocated"


def test_message_file_written_with_jsonio(tmp_path: Path) -> None:
    from helios import jsonio

    directory = _attempt(tmp_path)
    store = FakeBeads([Bead(id="b1")])
    msg_id, _ = messages.say(tmp_path, ".helios/runs", "b1", "hello", bead_store=store)
    path = directory.parent / "inbox" / f"{msg_id}.json"
    raw = path.read_text()
    assert raw.endswith("\n")
    payload = jsonio.loads(raw)
    assert payload["text"] == "hello"
    assert list(payload.keys()) == sorted(payload.keys())


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


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses permission bits")
def test_say_cli_unreadable_bead_directory_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    bad = tmp_path / ".helios" / "runs" / "b1"
    bad.mkdir(parents=True)
    bad.chmod(0o000)
    try:
        monkeypatch.chdir(tmp_path)
        assert cli.main(["say", "b1", "hi"]) == 1
        err = capsys.readouterr().err
        assert err.startswith("helios: ") and "Traceback" not in err
        assert len(err.splitlines()) == 1
    finally:
        bad.chmod(0o755)
    assert list(bad.iterdir()) == []
    assert not (tmp_path / ".helios" / "events.jsonl").exists()


@pytest.mark.parametrize("payload", [
    '{"a": 1}',
    "null",
    "[1]",
    '"str"',
    '[{"text": "x"}]',
    '[{"id": "c1", "issue_id": "b1", "author": "orchestrator", "text": "x", "extra": "z"}]',
    '[{"id": "c1", "issue_id": "b1", "author": "orchestrator", "text": 5}]',
])
def test_say_cli_shape_wrong_bd_comments_json_is_failed_comment_exit_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, payload: str
) -> None:
    _attempt(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    payload_file = bin_dir / "comments.json"
    payload_file.write_text(payload)
    fake_bd = bin_dir / "bd"
    fake_bd.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = comments ]; then /bin/cat "{payload_file}"; exit 0; fi\n'
        "exit 0\n"
    )
    fake_bd.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["say", "b1", "hi"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("helios: ") and "Traceback" not in err
    assert len(err.splitlines()) == 1
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


def _expected_cell(value: str) -> str:
    """The SPEC §9.2 text-output escaping, restated independently of `commands/ps.py`."""
    text = value.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def test_ps_text_escapes_backslash_control_and_surrogate_exactly(tmp_path: Path) -> None:
    _attempt(tmp_path, attempt_id="b1#\ud800", session_id="s\t1\n2")
    _input(tmp_path / ".helios" / "runs" / "b1" / "attempt-1", harness="back\\slash", worktree="/wt")
    proc = subprocess.run(
        [sys.executable, "-c", "import sys; from helios.cli import main; sys.exit(main(['ps']))"],
        cwd=tmp_path, capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.decode("utf-8").splitlines()
    assert len(lines) == 2
    columns = lines[1].split("\t")
    assert len(columns) == 9
    assert columns[3] == _expected_cell("back\\slash")
    assert columns[5] == _expected_cell("b1#\ud800")
    assert columns[8] == _expected_cell("back\\slash:s\t1\n2")


def test_ps_ascii_stdout_encoding_escapes_non_ascii_harness(tmp_path: Path) -> None:
    directory = _attempt(tmp_path)
    _input(directory, harness="héllo", worktree="/wt")
    env = dict(os.environ, PYTHONIOENCODING="ascii")
    proc = subprocess.run(
        [sys.executable, "-c", "import sys; from helios.cli import main; sys.exit(main(['ps']))"],
        cwd=tmp_path, capture_output=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.decode("ascii").splitlines()
    assert len(lines) == 2
    columns = lines[1].split("\t")
    assert columns[3] == "h\\xe9llo"


def test_ps_text_escapes_line_boundary_characters(tmp_path: Path) -> None:
    boundary = "\x0b\x0c\x1c\x1d\x1e\x85  "
    _attempt(tmp_path)
    _input(tmp_path / ".helios" / "runs" / "b1" / "attempt-1", harness=boundary, worktree="/wt")
    proc = subprocess.run(
        [sys.executable, "-c", "import sys; from helios.cli import main; sys.exit(main(['ps']))"],
        cwd=tmp_path, capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    stdout = proc.stdout.decode("utf-8")
    lines = stdout.splitlines()
    assert len(lines) == 2
    columns = lines[1].split("\t")
    assert columns[3] == "\\x0b\\x0c\\x1c\\x1d\\x1e\\x85\\u2028\\u2029"


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses permission bits")
def test_ps_unreadable_runs_directory_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    runs = tmp_path / ".helios" / "runs"
    runs.mkdir(parents=True)
    runs.chmod(0o000)
    try:
        monkeypatch.chdir(tmp_path)
        assert cli.main(["ps"]) == 1
        err = capsys.readouterr().err
        assert err.startswith("helios: ") and "Traceback" not in err
    finally:
        runs.chmod(0o755)


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses permission bits")
def test_ps_unreadable_bead_directory_is_skipped_others_still_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _attempt(tmp_path, "b1")
    bad = tmp_path / ".helios" / "runs" / "b2"
    bad.mkdir()
    bad.chmod(0o000)
    try:
        rows = sessions.rows(tmp_path, ".helios/runs", bead_store=FakeBeads([Bead(id="b1")]))
        assert [row["bead"] for row in rows] == ["b1"]
        monkeypatch.chdir(tmp_path)
        assert cli.main(["ps"]) == 0
    finally:
        bad.chmod(0o755)


def test_ps_reads_events_file_once_per_invocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(5):
        _attempt(tmp_path, f"b{i}")
    events.append(tmp_path, source="helios", type="launched", bead="b0", attempt="b0#1")
    store = FakeBeads([Bead(id=f"b{i}") for i in range(5)])
    calls: list[Path] = []
    original = Path.read_bytes

    def counting(self: Path) -> bytes:
        if self.name == "events.jsonl":
            calls.append(self)
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", counting)
    rows = sessions.rows(tmp_path, ".helios/runs", bead_store=store)
    assert len(rows) == 5
    assert len(calls) == 1


@pytest.mark.parametrize("bad_id", ["..", "a/b", "", "a\nb", "x" * 201])
@pytest.mark.parametrize("command", ["attach", "say", "stop"])
def test_invalid_bead_id_rejected_before_any_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, command: str, bad_id: str
) -> None:
    monkeypatch.chdir(tmp_path)
    args = [command, bad_id, "hi"] if command == "say" else [command, bad_id]
    assert cli.main(args) == 2
    assert capsys.readouterr().err == f"helios: invalid bead id {bad_id}\n"
    assert not (tmp_path / ".helios").exists()


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
