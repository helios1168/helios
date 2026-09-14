"""Harness adapter tests (SPEC §6.3, §6.4)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from helios.harness.agy import AgyAdapter
from helios.harness.base import LaunchSpec
from helios.harness.claude import ClaudeAdapter
from helios.harness.codex import CodexAdapter
from helios.harness.opencode import OpencodeAdapter

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "native"


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
