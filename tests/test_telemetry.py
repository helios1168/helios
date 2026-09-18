"""Telemetry export tests (SPEC section 21).

A fake OTLP/HTTP receiver on localhost records every POST it gets (path,
headers, protobuf body); tests decode the body with the real
``opentelemetry-proto`` message classes and assert exact span names, the
parent/child structure, attribute keys and the two required headers. The
rule that matters most, "export never fails a run", gets the most attention:
each hostile network case (a hung connection, connection refused, a 500, a
401, and a valid URL pointing at nothing) must leave ``helios run`` behaving
exactly as it does with telemetry disabled, bounded by ``timeout_s``.
"""

from __future__ import annotations

import base64
import http.server
import json
import socket
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

from helios import beads as beads_mod
from helios import config as config_mod
from helios import envelope as envelope_mod
from helios import run as run_mod
from helios.config import Config, ProjectConfig, TelemetryConfig
from helios.envelope import AgentReport, Envelope, ExecutionStatus, WorkStatus
from helios.merge import merge_bead

# ---------------------------------------------------------------------------
# Shared fixtures and helpers (this file is self-contained, matching the
# convention of the other test files: no cross-file imports).
# ---------------------------------------------------------------------------


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


def set_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")


class _Receiver:
    """A fake OTLP/HTTP traces endpoint that records every POST it gets."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        receiver = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                receiver.requests.append(
                    {"path": self.path, "headers": dict(self.headers), "body": body}
                )
                self.send_response(200)
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def endpoint(self, path: str = "/api/public/otel/v1/traces") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def receiver():
    r = _Receiver()
    try:
        yield r
    finally:
        r.shutdown()


def _spans(body: bytes) -> list:
    request = ExportTraceServiceRequest()
    request.ParseFromString(body)
    out = []
    for resource_spans in request.resource_spans:
        for scope_spans in resource_spans.scope_spans:
            out.extend(scope_spans.spans)
    return out


def _attrs(span) -> dict:
    result: dict[str, object] = {}
    for kv in span.attributes:
        value = kv.value
        result[kv.key] = getattr(value, value.WhichOneof("value"))
    return result


def _by_name(spans, name):
    for span in spans:
        if span.name == name:
            return span
    raise AssertionError(f"no span named {name!r} among {[s.name for s in spans]}")


# ---------------------------------------------------------------------------
# helios run: attempt trace shape.
# ---------------------------------------------------------------------------


def test_attempt_trace_shape(tmp_path, monkeypatch, receiver) -> None:
    hub = make_hub(tmp_path)
    bead = make_bead("b1", files=["src/"], test="mkdir -p src && echo hi > src/out.txt && exit 0")
    beads = beads_mod.FakeBeads([bead])
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 0, "stdout": "ok", "session_id": "s1",
         "report": {"status": "done", "summary": "did it"}},
    )
    set_fake(monkeypatch, script)
    set_credentials(monkeypatch)
    cfg = replace(
        config_mod.load(hub),
        telemetry=TelemetryConfig(enabled=True, endpoint=receiver.endpoint(), timeout_s=5),
    )
    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    assert rc == 0

    assert len(receiver.requests) == 1
    request = receiver.requests[0]
    assert request["path"] == "/api/public/otel/v1/traces"
    expected_auth = "Basic " + base64.b64encode(b"pk-test:sk-test").decode()
    assert request["headers"]["Authorization"] == expected_auth
    assert request["headers"]["x-langfuse-ingestion-version"] == "4"

    spans = _spans(request["body"])
    names = {s.name for s in spans}
    assert names == {"attempt", "launch", "check.test", "check.ownership", "validate", "commit"}

    root = _by_name(spans, "attempt")
    assert root.parent_span_id == b""
    for child_name in ("launch", "check.test", "check.ownership", "validate", "commit"):
        child = _by_name(spans, child_name)
        assert child.parent_span_id == root.span_id
        assert child.trace_id == root.trace_id

    root_attrs = _attrs(root)
    assert root_attrs["bead.id"] == "b1"
    assert root_attrs["bead.kind"] == "impl"
    assert root_attrs["harness"] == "fake"
    assert root_attrs["execution_status"] == "completed"
    assert root_attrs["terminal_state"] == "native_completed"
    assert root_attrs["session_id"] == "s1"
    assert "base_commit" in root_attrs
    assert "output_commit" in root_attrs

    test_check = _attrs(_by_name(spans, "check.test"))
    assert test_check["check.name"] == "test"
    assert test_check["passed"] is True


def test_attempt_child_order_matches_the_timestamps(tmp_path, monkeypatch, receiver) -> None:
    """SPEC section 21 lists launch, validate, check.<name>, commit, the order of section 7.1.

    The emitted order used to contradict the timestamps each span carried, because the children
    were built with validate after the checks while section 7.1 classifies at step 8 and runs the
    checks at step 9.
    """
    hub = make_hub(tmp_path)
    bead = make_bead("b1", files=["src/"], test="mkdir -p src && echo hi > src/out.txt && exit 0")
    beads = beads_mod.FakeBeads([bead])
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 0, "stdout": "ok", "session_id": "s1",
         "report": {"status": "done", "summary": "did it"}},
    )
    set_fake(monkeypatch, script)
    set_credentials(monkeypatch)
    cfg = replace(
        config_mod.load(hub),
        telemetry=TelemetryConfig(enabled=True, endpoint=receiver.endpoint(), timeout_s=5),
    )
    assert run_mod.run_one("b1", hub=hub, beads=beads, config=cfg, harness_override="fake") == 0

    spans = _spans(receiver.requests[0]["body"])
    root = _by_name(spans, "attempt")
    children = [s for s in spans if s.parent_span_id == root.span_id]

    assert [s.name for s in children] == [
        "launch",
        "validate",
        "check.test",
        "check.ownership",
        "commit",
    ]
    starts = [s.start_time_unix_nano for s in children]
    assert starts == sorted(starts), [(s.name, s.start_time_unix_nano) for s in children]


def test_disabled_makes_no_network_call(tmp_path, monkeypatch, receiver) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 0, "stdout": "ok", "session_id": "s1",
         "report": {"status": "done", "summary": "ok"}},
    )
    set_fake(monkeypatch, script)
    # telemetry.enabled defaults to false; the endpoint is still reachable,
    # so any request at all would prove a bug.
    cfg = replace(
        config_mod.load(hub),
        telemetry=TelemetryConfig(enabled=False, endpoint=receiver.endpoint(), timeout_s=5),
    )
    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    assert rc == 0
    assert receiver.requests == []


def test_missing_credentials_exports_nothing_not_refused(tmp_path, monkeypatch, receiver) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 0, "stdout": "ok", "session_id": "s1",
         "report": {"status": "done", "summary": "ok"}},
    )
    set_fake(monkeypatch, script)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    cfg = replace(
        config_mod.load(hub),
        telemetry=TelemetryConfig(enabled=True, endpoint=receiver.endpoint(), timeout_s=5),
    )
    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    assert rc == 0
    assert receiver.requests == []
    env = read_envelope(hub, "b1", 1)
    assert len(env["notes"]) == 1
    assert "LANGFUSE_PUBLIC_KEY" in env["notes"][0] or "LANGFUSE_SECRET_KEY" in env["notes"][0]


def test_empty_endpoint_refusal(tmp_path) -> None:
    hub = tmp_path / "hub"
    (hub / ".agents").mkdir(parents=True)
    (hub / ".agents" / "workflow.toml").write_text("[telemetry]\nenabled = true\n")
    with pytest.raises(ValueError, match=r"telemetry\.enabled requires telemetry\.endpoint"):
        config_mod.load(hub)


def test_telemetry_defaults_when_absent(tmp_path) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    cfg = config_mod.load(hub)
    assert cfg.telemetry == TelemetryConfig()
    assert cfg.telemetry.enabled is False
    assert cfg.telemetry.endpoint == ""
    assert cfg.telemetry.timeout_s == 5
    assert cfg.telemetry.service_name == "helios"


def test_telemetry_enabled_with_endpoint_loads(tmp_path) -> None:
    hub = tmp_path / "hub"
    (hub / ".agents").mkdir(parents=True)
    (hub / ".agents" / "workflow.toml").write_text(
        '[telemetry]\nenabled = true\nendpoint = "http://localhost:4318/v1/traces"\n'
        'timeout_s = 9\nservice_name = "svc"\n'
    )
    cfg = config_mod.load(hub)
    assert cfg.telemetry.enabled is True
    assert cfg.telemetry.endpoint == "http://localhost:4318/v1/traces"
    assert cfg.telemetry.timeout_s == 9
    assert cfg.telemetry.service_name == "svc"


# ---------------------------------------------------------------------------
# helios run: export never fails a run, under a hostile network.
# ---------------------------------------------------------------------------


def _bounded_case(tmp_path, monkeypatch, capsys, endpoint: str, *, timeout_s: int = 2):
    """Run the same bead once with telemetry disabled and once against
    ``endpoint`` with telemetry enabled; return (disabled_result, hostile_result,
    elapsed_seconds, hostile_notes).
    """
    set_credentials(monkeypatch)

    def _once(enabled: bool) -> tuple[int, str, str, dict]:
        case_dir = tmp_path / ("disabled" if not enabled else "hostile")
        hub = make_hub(case_dir)
        beads = beads_mod.FakeBeads([make_bead("b1", files=["src/"], test="true")])
        script = write_script(
            case_dir,
            {"exit_code": 0, "sleep_s": 0, "stdout": "ok", "session_id": "s1",
             "report": {"status": "done", "summary": "ok"}},
        )
        monkeypatch.setenv("HELIOS_FAKE_SCRIPT", str(script))
        telemetry = TelemetryConfig(
            enabled=enabled, endpoint=endpoint if enabled else "", timeout_s=timeout_s
        )
        cfg = replace(config_mod.load(hub), telemetry=telemetry)
        rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg, harness_override="fake")
        out = capsys.readouterr()
        env = read_envelope(hub, "b1", 1)
        return rc, out.out, out.err, env

    disabled = _once(False)
    t0 = time.monotonic()
    hostile = _once(True)
    elapsed = time.monotonic() - t0
    return disabled, hostile, elapsed


def test_hostile_connection_refused(tmp_path, monkeypatch, capsys) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    endpoint = f"http://127.0.0.1:{port}/api/public/otel/v1/traces"

    disabled, hostile, elapsed = _bounded_case(tmp_path, monkeypatch, capsys, endpoint)
    d_rc, d_out, d_err, d_env = disabled
    h_rc, h_out, h_err, h_env = hostile
    assert h_rc == d_rc
    assert h_out == d_out
    assert h_err == d_err
    assert h_env["execution_status"] == d_env["execution_status"]
    assert h_env["exit_code"] == d_env["exit_code"]
    assert h_env["checks"] == d_env["checks"]
    assert elapsed < 10
    assert len(h_env["notes"]) == 1
    assert h_env["notes"][0].startswith("telemetry:")
    assert d_env["notes"] == []


def test_hostile_hanging_connection(tmp_path, monkeypatch, capsys) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    port = server.getsockname()[1]
    stop = threading.Event()

    def _accept_and_hang() -> None:
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _addr = server.accept()
            except OSError:
                continue
            # Accept the connection and never respond, never close: the
            # hostile case named in the bead ("a connection that hangs
            # open").
            stop.wait()
            try:
                conn.close()
            except OSError:
                pass

    thread = threading.Thread(target=_accept_and_hang, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{port}/api/public/otel/v1/traces"
    try:
        disabled, hostile, elapsed = _bounded_case(tmp_path, monkeypatch, capsys, endpoint, timeout_s=2)
    finally:
        stop.set()
        server.close()
        thread.join(timeout=5)

    d_rc, d_out, d_err, d_env = disabled
    h_rc, h_out, h_err, h_env = hostile
    assert h_rc == d_rc
    assert h_out == d_out
    assert h_err == d_err
    assert h_env["checks"] == d_env["checks"]
    assert elapsed < 8
    assert len(h_env["notes"]) == 1


def test_hostile_500(tmp_path, monkeypatch, capsys) -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.send_response(500)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{port}/api/public/otel/v1/traces"
    try:
        disabled, hostile, elapsed = _bounded_case(tmp_path, monkeypatch, capsys, endpoint, timeout_s=3)
    finally:
        server.shutdown()
        server.server_close()

    d_rc, d_out, d_err, d_env = disabled
    h_rc, h_out, h_err, h_env = hostile
    assert h_rc == d_rc
    assert h_out == d_out
    assert h_err == d_err
    assert h_env["checks"] == d_env["checks"]
    assert elapsed < 10
    assert len(h_env["notes"]) == 1


def test_hostile_401(tmp_path, monkeypatch, capsys) -> None:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.send_response(401)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{port}/api/public/otel/v1/traces"
    try:
        disabled, hostile, elapsed = _bounded_case(tmp_path, monkeypatch, capsys, endpoint, timeout_s=3)
    finally:
        server.shutdown()
        server.server_close()

    d_rc, d_out, d_err, d_env = disabled
    h_rc, h_out, h_err, h_env = hostile
    assert h_rc == d_rc
    assert h_out == d_out
    assert h_err == d_err
    assert h_env["checks"] == d_env["checks"]
    assert elapsed < 5
    assert len(h_env["notes"]) == 1


def test_hostile_valid_url_pointing_at_nothing(tmp_path, monkeypatch, capsys) -> None:
    # RFC 5737 TEST-NET-3: reserved for documentation, never routable, so a
    # connection to it never gets a peer to respond or refuse.
    endpoint = "http://203.0.113.1:1/api/public/otel/v1/traces"
    disabled, hostile, elapsed = _bounded_case(tmp_path, monkeypatch, capsys, endpoint, timeout_s=2)

    d_rc, d_out, d_err, d_env = disabled
    h_rc, h_out, h_err, h_env = hostile
    assert h_rc == d_rc
    assert h_out == d_out
    assert h_err == d_err
    assert h_env["checks"] == d_env["checks"]
    assert elapsed < 8
    assert len(h_env["notes"]) == 1


# ---------------------------------------------------------------------------
# No confidential content ever reaches a span attribute.
# ---------------------------------------------------------------------------


def test_no_confidential_content_in_spans(tmp_path, monkeypatch, receiver) -> None:
    hub = make_hub(tmp_path)
    secret_summary = "the secret rollout plan for launch codes"
    bead = make_bead(
        "b1",
        files=["src/"],
        test="mkdir -p src/very/secret && echo hi > src/very/secret/path.txt && exit 0",
    )
    beads = beads_mod.FakeBeads([bead])
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 0, "stdout": "ok", "session_id": "s1",
         "report": {"status": "done", "summary": secret_summary}},
    )
    set_fake(monkeypatch, script)
    set_credentials(monkeypatch)
    cfg = replace(
        config_mod.load(hub),
        telemetry=TelemetryConfig(enabled=True, endpoint=receiver.endpoint(), timeout_s=5),
    )
    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    assert rc == 0
    assert len(receiver.requests) == 1
    body = receiver.requests[0]["body"]
    assert secret_summary.encode() not in body
    assert b"very/secret/path.txt" not in body
    for span in _spans(body):
        for value in _attrs(span).values():
            if isinstance(value, str):
                assert secret_summary not in value
                assert "very/secret/path.txt" not in value


# ---------------------------------------------------------------------------
# helios merge: trace shape and export never fails a merge.
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)
    return proc.stdout.strip()


def _merge_repo(tmp_path: Path) -> tuple[Path, Path]:
    hub = tmp_path / "repo"
    hub.mkdir(parents=True)
    _git(hub, "init", "-b", "main")
    _git(hub, "config", "user.email", "test@example.com")
    _git(hub, "config", "user.name", "Test")
    (hub / "value.txt").write_text("base\n")
    (hub / ".beads").mkdir()
    (hub / ".beads" / "issues.jsonl").write_text("{}\n")
    _git(hub, "add", ".")
    _git(hub, "commit", "-m", "base")
    worktree = tmp_path / "worktree" / "b1"
    worktree.parent.mkdir()
    _git(hub, "worktree", "add", "-b", "worktree-b1", str(worktree), "main")
    _git(hub, "worktree", "lock", "--reason", "keep", str(worktree))
    (worktree / "value.txt").write_text("merged\n")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", "change")
    return hub, worktree


def _merge_beads(hub: Path, worktree: Path) -> beads_mod.FakeBeads:
    impl = beads_mod.Bead(
        id="b1", kind="impl", unit="u1", status="closed",
        metadata={"output_commit": _git(worktree, "rev-parse", "HEAD")},
    )
    verify = beads_mod.Bead(
        id="v1", kind="verify-code", unit="u1", parent="b1", status="closed",
        metadata={"verdict": "verified", "attempt": 1}, labels=["unit:u1"],
    )
    bead_list = beads_mod.FakeBeads([impl, verify])
    envelope_dir = hub / ".helios" / "runs" / "v1" / "attempt-1"
    envelope_dir.mkdir(parents=True)
    (envelope_dir / "envelope.json").write_text(
        Envelope(
            task_id="v1", attempt=1, attempt_id="v1#1", kind="verify-code", harness="fake",
            started_at="now", base_commit=impl.metadata["output_commit"], input_hashes={},
            execution_status=ExecutionStatus.COMPLETED,
            report=AgentReport(status=WorkStatus.DONE, summary="ok"),
        ).model_dump_json()
    )
    return bead_list


def test_merge_trace_shape(tmp_path, monkeypatch, receiver) -> None:
    set_credentials(monkeypatch)
    hub, worktree = _merge_repo(tmp_path)
    beads = _merge_beads(hub, worktree)
    project = ProjectConfig(worktrees="../worktree")
    cfg = Config(
        hub=hub, project=project,
        telemetry=TelemetryConfig(enabled=True, endpoint=receiver.endpoint(), timeout_s=5),
    )
    rc, _msg = merge_bead(
        hub, "b1", project=project, config=cfg, beads=beads,
        check_runner=lambda _c, _w: 0, input_hashes=lambda _b: {},
    )
    assert rc == 0
    assert len(receiver.requests) == 1
    spans = _spans(receiver.requests[0]["body"])
    names = [s.name for s in spans]
    assert names.count("merge") == 2  # the root span and the step-6 child share the name
    assert "rebase" in names

    roots = [s for s in spans if s.parent_span_id == b""]
    assert len(roots) == 1
    root = roots[0]
    assert root.name == "merge"

    merge_children = [s for s in spans if s.name == "merge" and s is not root]
    assert len(merge_children) == 1
    assert merge_children[0].parent_span_id == root.span_id

    for name in ("rebase",):
        span = _by_name(spans, name)
        assert span.parent_span_id == root.span_id
        assert span.trace_id == root.trace_id
    root_attrs = _attrs(root)
    assert root_attrs["bead.id"] == "b1"


def test_merge_hostile_connection_refused_matches_disabled(tmp_path, monkeypatch) -> None:
    set_credentials(monkeypatch)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    endpoint = f"http://127.0.0.1:{port}/api/public/otel/v1/traces"

    project = ProjectConfig(worktrees="../worktree")

    hub_d, worktree_d = _merge_repo(tmp_path / "disabled")
    beads_d = _merge_beads(hub_d, worktree_d)
    cfg_d = Config(hub=hub_d, project=project, telemetry=TelemetryConfig(enabled=False))
    rc_d, msg_d = merge_bead(
        hub_d, "b1", project=project, config=cfg_d, beads=beads_d,
        check_runner=lambda _c, _w: 0, input_hashes=lambda _b: {},
    )

    hub_h, worktree_h = _merge_repo(tmp_path / "hostile")
    beads_h = _merge_beads(hub_h, worktree_h)
    cfg_h = Config(
        hub=hub_h, project=project,
        telemetry=TelemetryConfig(enabled=True, endpoint=endpoint, timeout_s=2),
    )
    t0 = time.monotonic()
    rc_h, msg_h = merge_bead(
        hub_h, "b1", project=project, config=cfg_h, beads=beads_h,
        check_runner=lambda _c, _w: 0, input_hashes=lambda _b: {},
    )
    elapsed = time.monotonic() - t0

    assert rc_h == rc_d
    assert msg_h == msg_d
    assert elapsed < 10

