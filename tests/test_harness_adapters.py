"""Harness adapter tests (SPEC §6.3, §6.4)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from helios.harness.agy import AgyAdapter
from helios.harness.base import LaunchSpec, NativeResult
from helios.harness.claude import ClaudeAdapter
from helios.harness.codex import CodexAdapter
from helios.harness.opencode import OpencodeAdapter

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "native"

ADAPTERS = {
    "claude": ClaudeAdapter(),
    "codex": CodexAdapter(),
    "opencode": OpencodeAdapter(),
    "agy": AgyAdapter(),
}


def make_spec(tmp_path: Path, **kw) -> LaunchSpec:
    worktree = tmp_path / "wt"
    worktree.mkdir(parents=True, exist_ok=True)
    raw = tmp_path / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    schema = tmp_path / "schema.json"
    if not schema.exists():
        schema.write_text('{"type":"object"}')
    base: dict = dict(
        bead="hel-7",
        attempt=2,
        worktree=worktree,
        prompt="Do the thing",
        report_path=tmp_path / "report.json",
        report_schema_path=schema,
        raw_dir=raw,
    )
    base.update(kw)
    return LaunchSpec(**base)


def full_spec(tmp_path: Path, **kw) -> LaunchSpec:
    return make_spec(
        tmp_path,
        model="m1",
        effort="high",
        extra_args=("foo", "bar"),
        **kw,
    )


# claude


def test_claude_argv_fresh(tmp_path: Path):
    spec = full_spec(tmp_path)
    spec.report_schema_path.write_text("SCHEMA-TEXT")
    assert ClaudeAdapter().argv(spec) == [
        "claude", "-p", "--output-format", "json", "--json-schema", "SCHEMA-TEXT",
        "--permission-mode", "bypassPermissions",
        "--model", "m1", "--effort", "high",
        "foo", "bar", "Do the thing",
    ]


def test_claude_argv_resume_and_minimal(tmp_path: Path):
    spec = full_spec(tmp_path, resume_session="sess-1")
    spec.report_schema_path.write_text("SCHEMA-TEXT")
    assert ClaudeAdapter().argv(spec) == [
        "claude", "-p", "--output-format", "json", "--json-schema", "SCHEMA-TEXT",
        "--permission-mode", "bypassPermissions",
        "--model", "m1", "--effort", "high",
        "--resume", "sess-1",
        "foo", "bar", "Do the thing",
    ]
    plain = make_spec(tmp_path)
    plain.report_schema_path.write_text("SCHEMA-TEXT")
    assert ClaudeAdapter().argv(plain) == [
        "claude", "-p", "--output-format", "json", "--json-schema", "SCHEMA-TEXT",
        "--permission-mode", "bypassPermissions", "Do the thing",
    ]


def test_claude_parse_fixtures(tmp_path: Path):
    spec = make_spec(tmp_path)
    fresh = ClaudeAdapter().parse(spec, 0, FIX / "claude" / "fresh.stdout")
    assert fresh.session_id == "1d14b8ca-3321-4924-ac98-1cbe827b6ff4"
    assert fresh.structured == {"status": "done", "summary": "pong"}
    assert fresh.native_error is None
    assert fresh.notes == ()
    resume = ClaudeAdapter().parse(spec, 0, FIX / "claude" / "resume.stdout")
    assert resume.session_id == fresh.session_id
    assert resume.structured == {"status": "blocked", "summary": "second turn"}
    assert resume.native_error is None
    error = ClaudeAdapter().parse(spec, 1, FIX / "claude" / "error.stdout")
    assert error.session_id == "0596241a-ab8e-4687-bddd-1dd2640df1cc"
    assert error.structured is None
    assert error.native_error
    assert "no-such-model-xyz" in error.native_error


def test_claude_parse_malformed(tmp_path: Path):
    spec = make_spec(tmp_path)
    missing = ClaudeAdapter().parse(spec, 0, tmp_path / "absent.stdout")
    assert missing.session_id is None
    assert missing.native_error == "no stdout"
    empty = tmp_path / "empty.stdout"
    empty.write_text("")
    assert ClaudeAdapter().parse(spec, 0, empty).native_error == "invalid JSON"
    cut = tmp_path / "cut.stdout"
    cut.write_text((FIX / "claude" / "fresh.stdout").read_text()[:120])
    assert ClaudeAdapter().parse(spec, 0, cut).native_error == "invalid JSON"
    text = tmp_path / "text.stdout"
    text.write_text("hello\n")
    bad = ClaudeAdapter().parse(spec, 0, text)
    assert bad.native_error == "invalid JSON"
    assert bad.structured is None


@pytest.mark.parametrize(
    "label,text",
    [
        ("top-level", '{"session_id":"s1","structured_output":1e999}'),
        ("nested-object", '{"session_id":"s1","structured_output":{"a":-1e999}}'),
        ("nested-array", '{"session_id":"s1","structured_output":{"a":[1e999]}}'),
    ],
)
def test_claude_parse_overflow_number(tmp_path: Path, label: str, text: str):
    spec = make_spec(tmp_path)
    path = tmp_path / f"overflow-{label}.stdout"
    path.write_text(text)
    result = ClaudeAdapter().parse(spec, 0, path)
    assert result.native_error == "invalid JSON", label
    assert result.structured is None, label


def test_claude_attach_and_stdin(tmp_path: Path):
    spec = make_spec(tmp_path)
    assert ClaudeAdapter().attach_command("sess-9", spec) == ["claude", "--resume", "sess-9"]
    assert ClaudeAdapter().stdin_text(spec) is None


# codex


def codex_spec_with_last_message(tmp_path: Path, name: str, **kw) -> LaunchSpec:
    spec = full_spec(tmp_path, **kw)
    shutil.copy(FIX / "codex" / name, spec.raw_dir / "last-message.json")
    return spec


def test_codex_argv_fresh(tmp_path: Path):
    spec = full_spec(tmp_path)
    assert CodexAdapter().argv(spec) == [
        "codex", "exec", "--json", "--output-schema", str(spec.report_schema_path),
        "-o", str(spec.raw_dir / "last-message.json"),
        "-C", str(spec.worktree),
        "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
        "-m", "m1", "-c", 'model_reasoning_effort="high"',
        "foo", "bar", "-",
    ]


def test_codex_argv_resume_and_minimal(tmp_path: Path):
    spec = full_spec(tmp_path, resume_session="sess-1")
    assert CodexAdapter().argv(spec) == [
        "codex", "exec", "resume", "sess-1",
        "--json", "--output-schema", str(spec.report_schema_path),
        "-o", str(spec.raw_dir / "last-message.json"),
        "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
        "-m", "m1", "-c", 'model_reasoning_effort="high"',
        "foo", "bar", "-",
    ]
    plain = make_spec(tmp_path)
    assert CodexAdapter().argv(plain) == [
        "codex", "exec", "--json", "--output-schema", str(plain.report_schema_path),
        "-o", str(plain.raw_dir / "last-message.json"),
        "-C", str(plain.worktree),
        "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
        "-",
    ]


def test_codex_parse_fixtures(tmp_path: Path):
    fresh_spec = codex_spec_with_last_message(tmp_path, "fresh-last-message.json")
    fresh = CodexAdapter().parse(fresh_spec, 0, FIX / "codex" / "fresh.stdout")
    assert fresh.session_id == "01a09c7e-ba04-7242-92a6-8fa259b9c563"
    assert fresh.structured == {"status": "done", "summary": "pong"}
    assert fresh.native_error is None
    assert fresh.notes == ()
    resume_spec = codex_spec_with_last_message(
        tmp_path, "resume-last-message.json", resume_session=fresh.session_id
    )
    resume = CodexAdapter().parse(resume_spec, 0, FIX / "codex" / "resume.stdout")
    assert resume.session_id == fresh.session_id
    assert resume.structured == {"status": "blocked", "summary": "second turn"}
    assert resume.native_error is None
    err_spec = make_spec(tmp_path)
    error = CodexAdapter().parse(err_spec, 1, FIX / "codex" / "error.stdout")
    assert error.session_id == "01a09c7f-ad35-7eb3-a71e-30c6c4abb857"
    assert error.structured is None
    assert error.native_error
    assert "no-such-model-xyz" in error.native_error


def test_codex_parse_malformed(tmp_path: Path):
    spec = make_spec(tmp_path)
    missing = CodexAdapter().parse(spec, 0, tmp_path / "absent.stdout")
    assert missing.session_id is None
    assert missing.native_error == "no stdout"
    assert missing.structured is None
    (tmp_path / "empty.stdout").write_text("")
    (tmp_path / "cut.stdout").write_text('{"type":"thread.started",')
    (tmp_path / "text.stdout").write_text("not json\n")
    for name in ("empty.stdout", "cut.stdout", "text.stdout"):
        bad = CodexAdapter().parse(spec, 0, tmp_path / name)
        assert bad.native_error == "no events", name
        assert bad.structured is None


def test_codex_structured_notes_and_warnings(tmp_path: Path):
    spec = make_spec(tmp_path)
    shutil.copy(FIX / "codex" / "fresh-last-message.json", spec.raw_dir / "last-message.json")
    dirty = tmp_path / "dirty.stdout"
    dirty.write_text("garbage\n\n" + (FIX / "codex" / "fresh.stdout").read_text())
    ok = CodexAdapter().parse(spec, 0, dirty)
    assert ok.native_error is None
    assert ok.structured == {"status": "done", "summary": "pong"}
    assert ok.notes == ("skipped 2 non-JSON lines",)
    bare = make_spec(tmp_path / "bare")
    bare_stdout = tmp_path / "bare.stdout"
    bare_stdout.write_text((FIX / "codex" / "fresh.stdout").read_text())
    res = CodexAdapter().parse(bare, 0, bare_stdout)
    assert res.native_error is None
    assert res.structured is None
    assert res.notes == ("no structured result",)
    (bare.raw_dir / "last-message.json").write_text("[1, 2]\n")
    res2 = CodexAdapter().parse(bare, 0, bare_stdout)
    assert res2.structured is None
    assert res2.notes == ("structured result is not a JSON object",)
    warn = tmp_path / "warn.stdout"
    warn.write_text(
        '{"type":"thread.started","thread_id":"t1"}\n'
        '{"type":"item.completed","item":{"type":"error","message":"warn"}}\n'
        '{"type":"turn.started"}\n'
        '{"type":"turn.completed"}\n'
    )
    warned = CodexAdapter().parse(bare, 0, warn)
    assert warned.native_error is None
    assert warned.session_id == "t1"
    incomplete = tmp_path / "incomplete.stdout"
    incomplete.write_text('{"type":"thread.started","thread_id":"t2"}\n{"type":"turn.started"}\n')
    lost = CodexAdapter().parse(bare, 0, incomplete)
    assert lost.session_id == "t2"
    assert lost.native_error == "turn did not complete"


def test_codex_parse_overflow_number_in_line(tmp_path: Path):
    spec = make_spec(tmp_path)
    shutil.copy(FIX / "codex" / "fresh-last-message.json", spec.raw_dir / "last-message.json")
    dirty = tmp_path / "dirty.stdout"
    dirty.write_text(
        '{"type":"thread.started","thread_id":"bad","huge":1e999}\n'
        + (FIX / "codex" / "fresh.stdout").read_text()
    )
    ok = CodexAdapter().parse(spec, 0, dirty)
    assert ok.native_error is None
    assert ok.structured == {"status": "done", "summary": "pong"}
    assert ok.notes == ("skipped 1 non-JSON lines",)
    assert ok.session_id != "bad"


def test_codex_last_message_overflow_number(tmp_path: Path):
    bare = make_spec(tmp_path)
    bare_stdout = tmp_path / "bare.stdout"
    bare_stdout.write_text((FIX / "codex" / "fresh.stdout").read_text())
    (bare.raw_dir / "last-message.json").write_text('{"a": -1e999}\n')
    res = CodexAdapter().parse(bare, 0, bare_stdout)
    assert res.structured is None
    assert res.notes == ("structured result is not a JSON object",)


def test_codex_attach_and_stdin(tmp_path: Path):
    spec = make_spec(tmp_path)
    assert CodexAdapter().attach_command("sess-9", spec) == ["codex", "resume", "sess-9"]
    assert CodexAdapter().stdin_text(spec) == "Do the thing"


# opencode


def test_opencode_argv_fresh_and_resume(tmp_path: Path):
    spec = full_spec(tmp_path, server_url="http://127.0.0.1:5678")
    work = str(spec.worktree)
    assert OpencodeAdapter().argv(spec) == [
        "opencode", "run", "--format", "json", "--dir", work,
        "--title", "hel-7#2",
        "-m", "m1", "--variant", "high", "--attach", "http://127.0.0.1:5678",
        "--auto", "foo", "bar", "Do the thing",
    ]
    resumed = full_spec(tmp_path, server_url="http://127.0.0.1:5678",
                        resume_session="ses_1")
    assert OpencodeAdapter().argv(resumed) == [
        "opencode", "run", "--format", "json", "--dir", work,
        "--title", "hel-7#2",
        "-m", "m1", "--variant", "high", "--attach", "http://127.0.0.1:5678",
        "-s", "ses_1",
        "--auto", "foo", "bar", "Do the thing",
    ]
    plain = make_spec(tmp_path)
    assert OpencodeAdapter().argv(plain) == [
        "opencode", "run", "--format", "json", "--dir", str(plain.worktree),
        "--title", "hel-7#2", "--auto", "Do the thing",
    ]


def test_opencode_parse_fixtures(tmp_path: Path):
    spec = make_spec(tmp_path)
    fresh = OpencodeAdapter().parse(spec, 0, FIX / "opencode" / "fresh.stdout")
    assert fresh.session_id == "ses_f63856fb7ffes5GOJsCid8tQPY"
    assert fresh.structured is None
    assert fresh.native_error is None
    tools = OpencodeAdapter().parse(spec, 0, FIX / "opencode" / "tools.stdout")
    assert tools.session_id == "ses_f63819670ffeFzeH9hHlMKzWKU"
    assert tools.native_error is None
    resume = OpencodeAdapter().parse(spec, 0, FIX / "opencode" / "resume.stdout")
    assert resume.session_id == "ses_f63819670ffeFzeH9hHlMKzWKU"
    assert resume.native_error is None
    error = OpencodeAdapter().parse(spec, 1, FIX / "opencode" / "error.stdout")
    assert error.session_id == "ses_f637fb102ffeVkpLJhLshoFXNm"
    assert error.structured is None
    assert error.native_error == "Unexpected server error. Check server logs for details."


def test_opencode_parse_malformed(tmp_path: Path):
    spec = make_spec(tmp_path)
    missing = OpencodeAdapter().parse(spec, 0, tmp_path / "absent.stdout")
    assert missing.session_id is None
    assert missing.native_error == "no stdout"
    (tmp_path / "empty.stdout").write_text("")
    (tmp_path / "cut.stdout").write_text('{"type":"step_start",')
    (tmp_path / "text.stdout").write_text("boom\n")
    for name in ("empty.stdout", "cut.stdout", "text.stdout"):
        bad = OpencodeAdapter().parse(spec, 0, tmp_path / name)
        assert bad.native_error == "no events", name
        assert bad.structured is None
    partial = tmp_path / "partial.stdout"
    partial.write_text(
        '{"type":"step_start","sessionID":"s1","part":{}}\n'
        '{"type":"text","sessionID":"s1","part":{}}\n'
    )
    lost = OpencodeAdapter().parse(spec, 0, partial)
    assert lost.session_id == "s1"
    assert lost.native_error == "turn did not complete"
    dirty = tmp_path / "dirty.stdout"
    dirty.write_text("oops\n" + (FIX / "opencode" / "fresh.stdout").read_text())
    ok = OpencodeAdapter().parse(spec, 0, dirty)
    assert ok.native_error is None
    assert ok.notes == ("skipped 1 non-JSON lines",)


def test_opencode_parse_overflow_number_in_line(tmp_path: Path):
    spec = make_spec(tmp_path)
    dirty = tmp_path / "dirty.stdout"
    dirty.write_text(
        '{"type":"step_start","sessionID":"bad","huge":1e999}\n'
        + (FIX / "opencode" / "fresh.stdout").read_text()
    )
    ok = OpencodeAdapter().parse(spec, 0, dirty)
    assert ok.native_error is None
    assert ok.notes == ("skipped 1 non-JSON lines",)
    assert ok.session_id != "bad"


def test_opencode_attach_and_stdin(tmp_path: Path):
    spec = make_spec(tmp_path, server_url="http://127.0.0.1:5678")
    assert OpencodeAdapter().attach_command("ses_9", spec) == [
        "opencode", "attach", "http://127.0.0.1:5678",
        "--dir", str(spec.worktree), "-s", "ses_9",
    ]
    plain = make_spec(tmp_path)
    assert OpencodeAdapter().attach_command("ses_9", plain) == [
        "opencode", str(plain.worktree), "-s", "ses_9",
    ]
    assert OpencodeAdapter().stdin_text(plain) is None


# agy


def test_agy_argv_fresh_and_resume(tmp_path: Path):
    spec = full_spec(tmp_path)
    assert AgyAdapter().argv(spec) == [
        "agy", "--output-format", "json", "--json-schema", str(spec.report_schema_path),
        "--dangerously-skip-permissions",
        "--model", "m1", "--effort", "high",
        "foo", "bar", "-p", "Do the thing",
    ]
    resumed = full_spec(tmp_path, resume_session="conv-1")
    assert AgyAdapter().argv(resumed) == [
        "agy", "--output-format", "json", "--json-schema", str(resumed.report_schema_path),
        "--dangerously-skip-permissions",
        "--model", "m1", "--effort", "high",
        "--conversation", "conv-1",
        "foo", "bar", "-p", "Do the thing",
    ]
    plain = make_spec(tmp_path)
    assert AgyAdapter().argv(plain) == [
        "agy", "--output-format", "json", "--json-schema", str(plain.report_schema_path),
        "--dangerously-skip-permissions", "-p", "Do the thing",
    ]


def test_agy_parse_fixtures(tmp_path: Path):
    spec = make_spec(tmp_path)
    fresh = AgyAdapter().parse(spec, 0, FIX / "agy" / "fresh.stdout")
    assert fresh.session_id == "dd642d3c-cbeb-46b8-8860-53f14fc2e497"
    assert fresh.structured == {"status": "done", "summary": "pong"}
    assert fresh.native_error is None
    assert fresh.notes == ()
    resume = AgyAdapter().parse(spec, 0, FIX / "agy" / "resume.stdout")
    assert resume.session_id == fresh.session_id
    assert resume.structured == {"status": "blocked", "summary": "second turn"}
    assert resume.native_error is None
    error = AgyAdapter().parse(spec, 1, FIX / "agy" / "error.stdout")
    assert error.session_id is None
    assert error.structured is None
    assert error.native_error
    assert "no-such-model-xyz" in error.native_error


def test_agy_parse_malformed(tmp_path: Path):
    spec = make_spec(tmp_path)
    missing = AgyAdapter().parse(spec, 0, tmp_path / "absent.stdout")
    assert missing.session_id is None
    assert missing.native_error == "no stdout"
    (tmp_path / "empty.stdout").write_text("")
    (tmp_path / "cut.stdout").write_text(
        (FIX / "agy" / "fresh.stdout").read_text()[:80]
    )
    (tmp_path / "text.stdout").write_text("hello\n")
    (tmp_path / "array.stdout").write_text("[1,2]\n")
    for name in ("empty.stdout", "cut.stdout", "text.stdout", "array.stdout"):
        bad = AgyAdapter().parse(spec, 0, tmp_path / name)
        assert bad.native_error == "invalid JSON", name
        assert bad.structured is None


@pytest.mark.parametrize(
    "label,text",
    [
        ("top-level", '{"conversation_id":"c1","status":"SUCCESS","structured_output":1e999}'),
        (
            "nested-object",
            '{"conversation_id":"c1","status":"SUCCESS","structured_output":{"a":-1e999}}',
        ),
        (
            "nested-array",
            '{"conversation_id":"c1","status":"SUCCESS","structured_output":{"a":[1e999]}}',
        ),
    ],
)
def test_agy_parse_overflow_number(tmp_path: Path, label: str, text: str):
    spec = make_spec(tmp_path)
    path = tmp_path / f"overflow-{label}.stdout"
    path.write_text(text)
    result = AgyAdapter().parse(spec, 0, path)
    assert result.native_error == "invalid JSON", label
    assert result.structured is None, label


def test_agy_attach_and_stdin(tmp_path: Path):
    spec = make_spec(tmp_path)
    assert AgyAdapter().attach_command("conv-9", spec) == ["agy", "--conversation", "conv-9"]
    assert AgyAdapter().stdin_text(spec) is None


def test_exit_none_is_not_an_error(tmp_path: Path):
    spec = make_spec(tmp_path)
    fresh = ClaudeAdapter().parse(spec, None, FIX / "claude" / "fresh.stdout")
    assert fresh.native_error is None
    assert fresh.structured == {"status": "done", "summary": "pong"}


# live probes, run by hand only


@pytest.mark.live
def test_live_claude():
    binary = shutil.which("claude")
    if binary is None:
        pytest.skip("claude binary not installed")
    done = subprocess.run([binary, "--version"], capture_output=True, timeout=60)
    assert done.returncode == 0


@pytest.mark.live
def test_live_codex():
    binary = shutil.which("codex")
    if binary is None:
        pytest.skip("codex binary not installed")
    done = subprocess.run([binary, "--version"], capture_output=True, timeout=60)
    assert done.returncode == 0


@pytest.mark.live
def test_live_opencode():
    binary = shutil.which("opencode")
    if binary is None:
        pytest.skip("opencode binary not installed")
    done = subprocess.run([binary, "--version"], capture_output=True, timeout=60)
    assert done.returncode == 0


@pytest.mark.live
def test_live_agy():
    binary = shutil.which("agy")
    if binary is None:
        pytest.skip("agy binary not installed")
    done = subprocess.run([binary, "--version"], capture_output=True, timeout=60)
    assert done.returncode == 0


def test_fixture_values_are_json_objects():
    for name in ("claude/fresh.stdout", "agy/fresh.stdout"):
        obj = json.loads((FIX / name).read_text())
        assert isinstance(obj, dict), name


# byte-level input handling (SPEC §6.4)


@pytest.mark.parametrize("name", ["claude", "codex", "opencode", "agy"])
@pytest.mark.parametrize("payload", [b"\x80", b"\xff\xfe garbage\n"])
def test_parse_invalid_utf8_stdout(name, payload, tmp_path: Path):
    spec = make_spec(tmp_path)
    out = tmp_path / "bad.stdout"
    out.write_bytes(payload)
    got = ADAPTERS[name].parse(spec, 0, out)
    assert got.structured is None
    if name in ("claude", "agy"):
        assert got.native_error == "invalid JSON"
    else:
        assert got.native_error == "no events"
        assert got.notes == ("skipped 1 non-JSON lines",)


@pytest.mark.parametrize("name", ["claude", "codex", "opencode", "agy"])
def test_parse_directory_stdout(name, tmp_path: Path):
    spec = make_spec(tmp_path)
    d = tmp_path / "dir.stdout"
    d.mkdir()
    got = ADAPTERS[name].parse(spec, 0, d)
    assert got == NativeResult(None, None, "no stdout")


def test_codex_last_message_invalid_utf8(tmp_path: Path):
    spec = make_spec(tmp_path)
    (spec.raw_dir / "last-message.json").write_bytes(b'{"a":"\xff"}')
    got = CodexAdapter().parse(spec, 0, FIX / "codex" / "fresh.stdout")
    assert got.native_error is None
    assert got.structured is None
    assert got.notes == ("structured result is not a JSON object",)


HS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)


@pytest.mark.parametrize("name", ["claude", "codex", "opencode", "agy"])
@HS
@given(data=st.binary(max_size=600), code=st.one_of(st.none(), st.integers(-300, 300)))
def test_parse_random_bytes_never_raises(name, data, code, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rb")
    spec = make_spec(tmp)
    out = tmp / "out.stdout"
    out.write_bytes(data)
    (spec.raw_dir / "last-message.json").write_bytes(data)
    got = ADAPTERS[name].parse(spec, code, out)
    assert isinstance(got, NativeResult)
    if got.native_error is not None:
        assert got.structured is None and got.native_error


@pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\x85"])
def test_codex_unicode_separator_inside_string(sep, tmp_path: Path):
    lines = (FIX / "codex" / "fresh.stdout").read_text().splitlines()
    item = json.loads(lines[2])
    item["item"]["text"] = f"a{sep}b"
    lines[2] = json.dumps(item, ensure_ascii=False)
    out = tmp_path / "u.stdout"
    out.write_bytes(("\n".join(lines) + "\n").encode())
    spec = make_spec(tmp_path)
    (spec.raw_dir / "last-message.json").write_bytes(b"{}")
    got = CodexAdapter().parse(spec, 0, out)
    assert got.native_error is None, got
    assert got.notes == ()


@pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\x85"])
def test_opencode_unicode_separator_inside_string(sep, tmp_path: Path):
    lines = (FIX / "opencode" / "fresh.stdout").read_text().splitlines()
    fin = json.loads(lines[2])
    fin["part"]["reason"] = f"stop{sep}"
    lines[2] = json.dumps(fin, ensure_ascii=False)
    out = tmp_path / "u.stdout"
    out.write_bytes(("\n".join(lines) + "\n").encode())
    got = OpencodeAdapter().parse(make_spec(tmp_path), 0, out)
    assert got.native_error is None, got
    assert got.notes == ()


def test_codex_error_message_with_separator(tmp_path: Path):
    text = (FIX / "codex" / "error.stdout").read_text()
    data = text.replace("not supported", "not\u2028supported")
    for line in data.split("\n"):
        if line.strip():
            assert isinstance(json.loads(line), dict)
    out = tmp_path / "e.stdout"
    out.write_bytes(data.encode())
    got = CodexAdapter().parse(make_spec(tmp_path), 1, out)
    assert got.native_error and "supported" in got.native_error, got


def test_opencode_error_message_with_separator(tmp_path: Path):
    text = (FIX / "opencode" / "error.stdout").read_text()
    data = text.replace("Unexpected server error.", "Unexpected\u2028server error.")
    for line in data.split("\n"):
        if line.strip():
            assert isinstance(json.loads(line), dict)
    out = tmp_path / "e.stdout"
    out.write_bytes(data.encode())
    got = OpencodeAdapter().parse(make_spec(tmp_path), 1, out)
    assert got.session_id == "ses_f637fb102ffeVkpLJhLshoFXNm", got
    assert got.native_error == "Unexpected\u2028server error. Check server logs for details.", got


@pytest.mark.parametrize("name", ["claude", "agy"])
def test_structured_output_null_is_not_an_object(name, tmp_path: Path):
    obj = json.loads((FIX / name / "fresh.stdout").read_text())
    obj["structured_output"] = None
    out = tmp_path / "n.stdout"
    out.write_text(json.dumps(obj))
    got = ADAPTERS[name].parse(make_spec(tmp_path), 0, out)
    assert got.native_error is None
    assert got.structured is None
    assert got.notes == ("structured result is not a JSON object",)


def test_codex_session_skips_non_string_thread_id(tmp_path: Path):
    out = tmp_path / "s.stdout"
    out.write_bytes(
        b'{"type":"thread.started","thread_id":7}\n'
        b'{"type":"thread.started","thread_id":"B"}\n'
        b'{"type":"turn.completed"}\n'
    )
    spec = make_spec(tmp_path)
    (spec.raw_dir / "last-message.json").write_bytes(b"{}")
    got = CodexAdapter().parse(spec, 0, out)
    assert got.native_error is None
    assert got.session_id == "B"
    assert got.structured == {}


def test_opencode_session_skips_non_string_id(tmp_path: Path):
    out = tmp_path / "s.stdout"
    out.write_bytes(
        b'{"type":"step_start","sessionID":42,"part":{}}\n'
        b'{"type":"text","sessionID":"real","part":{}}\n'
        b'{"type":"step_finish","sessionID":"real","part":{}}\n'
    )
    got = OpencodeAdapter().parse(make_spec(tmp_path), 0, out)
    assert got.native_error is None
    assert got.session_id == "real"


@pytest.mark.parametrize("value", ["true", 1])
def test_claude_is_error_non_bool_is_not_failure(value, tmp_path: Path):
    out = tmp_path / "b.stdout"
    out.write_text(json.dumps({
        "session_id": "s1", "is_error": value, "result": "r",
        "structured_output": {"a": 1},
    }))
    got = ClaudeAdapter().parse(make_spec(tmp_path), 0, out)
    assert got.native_error is None
    assert got.session_id == "s1"
    assert got.structured == {"a": 1}


# regular-file gate, NUL paths, JSON-whitespace strip, constants (SPEC §6.4)


def _parse_in_child(
    name: str,
    stdout_path: Path,
    raw_dir: Path,
    writer_payload: bytes | None = None,
    writer_target: Path | None = None,
    timeout: float = 10.0,
) -> str:
    """Run one parse in a child process so a FIFO open cannot hang the suite."""
    cls = {
        "claude": "ClaudeAdapter",
        "codex": "CodexAdapter",
        "opencode": "OpencodeAdapter",
        "agy": "AgyAdapter",
    }[name]
    script = (
        "import threading\n"
        "from pathlib import Path\n"
        f"from helios.harness.{name} import {cls} as A\n"
        "from helios.harness.base import LaunchSpec\n"
        f"payload = {writer_payload!r}\n"
        f"target = {str(writer_target)!r} if {writer_target is not None} else None\n"
        "if payload is not None and target is not None:\n"
        "    def _w():\n"
        "        with open(target, 'wb') as _f:\n"
        "            _f.write(payload)\n"
        "    threading.Thread(target=_w, daemon=True).start()\n"
        "spec = LaunchSpec(bead='b', attempt=1, worktree=Path('.'), prompt='p',\n"
        "                  report_path=Path('r'), report_schema_path=Path('s'),\n"
        f"                  raw_dir=Path({str(raw_dir)!r}))\n"
        f"print(repr(A().parse(spec, 0, Path({str(stdout_path)!r}))), flush=True)\n"
    )
    try:
        done = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{name} parse hung on {stdout_path}")
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


NO_STDOUT_REPR = (
    "NativeResult(session_id=None, structured=None, "
    "native_error='no stdout', notes=())"
)


@pytest.mark.parametrize("name", ["claude", "codex", "opencode", "agy"])
def test_stdout_fifo_without_writer(name, tmp_path: Path):
    fifo = tmp_path / "fifo.stdout"
    os.mkfifo(fifo)
    assert _parse_in_child(name, fifo, tmp_path / "raw") == NO_STDOUT_REPR


@pytest.mark.parametrize("name", ["claude", "codex", "opencode", "agy"])
def test_stdout_fifo_with_writer_is_not_opened(name, tmp_path: Path):
    fifo = tmp_path / "fifo.stdout"
    os.mkfifo(fifo)
    payload = (FIX / name / "fresh.stdout").read_bytes()
    assert _parse_in_child(name, fifo, tmp_path / "raw", payload, fifo) == NO_STDOUT_REPR


def test_last_message_fifo_without_writer(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()
    os.mkfifo(raw / "last-message.json")
    out = tmp_path / "o.stdout"
    out.write_bytes((FIX / "codex/fresh.stdout").read_bytes())
    res = _parse_in_child("codex", out, raw)
    assert "no structured result" in res and "structured=None" in res


def test_last_message_fifo_with_writer_is_not_opened(tmp_path: Path):
    raw = tmp_path / "raw"
    raw.mkdir()
    os.mkfifo(raw / "last-message.json")
    out = tmp_path / "o.stdout"
    out.write_bytes((FIX / "codex/fresh.stdout").read_bytes())
    res = _parse_in_child("codex", out, raw, b'{"status":"done"}', raw / "last-message.json")
    assert "no structured result" in res and "structured=None" in res


@pytest.mark.parametrize("name", ["claude", "codex", "opencode", "agy"])
def test_nul_in_stdout_path(name, tmp_path: Path):
    got = ADAPTERS[name].parse(make_spec(tmp_path), 0, tmp_path / "a\x00b")
    assert got == NativeResult(None, None, "no stdout")


def test_nul_in_raw_dir(tmp_path: Path):
    spec = make_spec(tmp_path, raw_dir=tmp_path / "r\x00w")
    got = CodexAdapter().parse(spec, 0, FIX / "codex/fresh.stdout")
    assert got.native_error is None
    assert got.structured is None
    assert got.notes == ("no structured result",)


@pytest.mark.parametrize("payload", [
    '{"type":"step_finish","sessionID":"s"}\u2028\n',
    '\xa0{"type":"step_finish","sessionID":"s"}\n',
    '{"type":"step_finish","sessionID":"s"}\u0085\n',
])
def test_opencode_strip_is_json_whitespace_only(payload, tmp_path: Path):
    out = tmp_path / "w.stdout"
    out.write_bytes(payload.encode("utf-8"))
    got = OpencodeAdapter().parse(make_spec(tmp_path), 0, out)
    assert got.session_id is None
    assert got.native_error == "no events"
    assert got.notes == ("skipped 1 non-JSON lines",)


def test_codex_trailing_separator_on_every_line(tmp_path: Path):
    data = (FIX / "codex/fresh.stdout").read_bytes().replace(b"\n", "\u2029\n".encode("utf-8"))
    out = tmp_path / "w.stdout"
    out.write_bytes(data)
    spec = make_spec(tmp_path)
    (spec.raw_dir / "last-message.json").write_bytes(b"{}")
    got = CodexAdapter().parse(spec, 0, out)
    assert got.session_id is None
    assert got.native_error == "no events"


def test_claude_trailing_separator_is_invalid_json(tmp_path: Path):
    out = tmp_path / "w.stdout"
    out.write_bytes((FIX / "claude/fresh.stdout").read_bytes() + "\u2028".encode("utf-8"))
    got = ClaudeAdapter().parse(make_spec(tmp_path), 0, out)
    assert got.native_error == "invalid JSON"
    assert got.structured is None


def test_agy_leading_ideographic_space_is_invalid_json(tmp_path: Path):
    out = tmp_path / "w.stdout"
    out.write_bytes("\u3000".encode("utf-8") + (FIX / "agy/fresh.stdout").read_bytes())
    got = AgyAdapter().parse(make_spec(tmp_path), 0, out)
    assert got.native_error == "invalid JSON"
    assert got.structured is None


def test_last_message_trailing_nbsp_is_not_an_object(tmp_path: Path):
    out = tmp_path / "w.stdout"
    out.write_bytes((FIX / "codex/fresh.stdout").read_bytes())
    spec = make_spec(tmp_path)
    (spec.raw_dir / "last-message.json").write_bytes('{"status":"done"}\xa0'.encode("utf-8"))
    got = CodexAdapter().parse(spec, 0, out)
    assert got.native_error is None
    assert got.structured is None
    assert got.notes == ("structured result is not a JSON object",)


def test_nan_line_is_skipped(tmp_path: Path):
    out = tmp_path / "n.stdout"
    out.write_bytes(b'{"type":"step_finish","sessionID":"s"}\n{"x":NaN}\n')
    got = OpencodeAdapter().parse(make_spec(tmp_path), 0, out)
    assert got.native_error is None
    assert got.session_id == "s"
    assert got.notes == ("skipped 1 non-JSON lines",)


@pytest.mark.parametrize("name", ["claude", "agy"])
def test_nan_document_is_invalid_json(name, tmp_path: Path):
    out = tmp_path / "n.stdout"
    out.write_bytes(
        b'{"session_id":"s","conversation_id":"s","is_error":false,'
        b'"status":"SUCCESS","x":NaN}'
    )
    got = ADAPTERS[name].parse(make_spec(tmp_path), 0, out)
    assert got.native_error == "invalid JSON"
    assert got.structured is None


def test_nan_last_message_is_not_an_object(tmp_path: Path):
    out = tmp_path / "n.stdout"
    out.write_bytes((FIX / "codex/fresh.stdout").read_bytes())
    spec = make_spec(tmp_path)
    (spec.raw_dir / "last-message.json").write_bytes(b'{"x":NaN}')
    got = CodexAdapter().parse(spec, 0, out)
    assert got.native_error is None
    assert got.structured is None
    assert got.notes == ("structured result is not a JSON object",)


def test_agy_missing_status_is_failure(tmp_path: Path):
    out = tmp_path / "m.stdout"
    out.write_text(json.dumps({"conversation_id": "c1", "structured_output": {"a": 1}}))
    got = AgyAdapter().parse(make_spec(tmp_path), 0, out)
    assert got.session_id == "c1"
    assert got.structured is None
    assert got.native_error == "turn did not complete"
