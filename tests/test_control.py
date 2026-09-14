from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

from helios import control
from helios.beads import Bead, FakeBeads
from helios.envelope import AgentReport, Envelope, Finding


def finding(verdict: str) -> Finding:
    return Finding(id="f1", claim="c", verdict=verdict, method="review", scope="instance", bound={"n": "1"}, artifact="a")  # type: ignore[arg-type]


def mk_envelope(
    bead_id: str,
    *,
    attempt: int = 1,
    kind: str = "impl",
    execution_status: str = "completed",
    status: str | None = "done",
    verdict: str | None = None,
    summary: str | None = "ok",
    with_report: bool = True,
) -> Envelope:
    """A real Envelope, built through the pydantic models (not a dict)."""
    report = None
    if with_report and status is not None:
        findings = [finding(verdict)] if verdict is not None else []
        report = AgentReport(status=status, summary=summary or "", findings=findings)  # type: ignore[arg-type]
    return Envelope(
        task_id=bead_id,
        attempt=attempt,
        attempt_id=f"{bead_id}#{attempt}",
        kind=kind,
        harness="fake",
        started_at="2026-01-01T00:00:00Z",
        base_commit="0" * 40,
        input_hashes={},
        execution_status=execution_status,  # type: ignore[arg-type]
        report=report,
    )


def bead(name: str, kind: str = "impl", *, unit: str | None = "u", status: str = "open") -> Bead:
    labels = [f"kind:{kind}"] + ([f"unit:{unit}"] if unit is not None else [])
    return Bead(name, kind=kind, status=status, labels=labels)


class ReadyInBdOrder(FakeBeads):
    def __init__(self, rows: list[Bead]) -> None:
        super().__init__(rows)
        self.rows = rows

    def ready(self, *, labels: list[str] = []) -> list[Bead]:
        return self.rows


def test_candidates_filter_status_kind_unit_and_keep_bd_order() -> None:
    rows = [
        bead("closed", status="closed"),
        Bead("bogus", kind="impl", labels=["kind:bogus", "unit:u"]),
        Bead("no-kind", kind="impl", labels=["unit:u"]),
        bead("other-unit", unit="v"),
        bead("second"),
        bead("first"),
    ]
    assert [item.id for item in control.candidates(ReadyInBdOrder(rows), "u")] == ["second", "first"]


def test_next_skips_stop_at_and_passes_run_code(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[str] = []
    rows = [bead("manual", "model"), bead("run", "impl")]
    result = control.next_bead(ReadyInBdOrder(rows), unit="u", stop_at=("model",), run=lambda item: calls.append(item.id) or 7, read_envelope=lambda item: mk_envelope(item.id))
    assert result == 7
    assert calls == ["run"]
    assert capsys.readouterr().out == "run#1\tcompleted\tdone\t-\tok\n"


def test_next_line_verdict_is_overall_verdict_of_findings(capsys: pytest.CaptureFixture[str]) -> None:
    """SPEC section 4.2/11: the verdict column is overall_verdict(findings), never a
    ``report["verdict"]`` field, which AgentReport does not have."""
    rows = [bead("v", "verify-code")]
    result = control.next_bead(ReadyInBdOrder(rows), unit="u", stop_at=(), run=lambda _item: 0, read_envelope=lambda item: mk_envelope(item.id, kind="verify-code", verdict="inconclusive"))
    assert result == 0
    assert capsys.readouterr().out == "v#1\tcompleted\tdone\tinconclusive\tok\n"


def test_next_envelope_line_uses_dashes_when_no_report(capsys: pytest.CaptureFixture[str]) -> None:
    """A missing report (execution failed before one was captured) shows '-' for status,
    verdict and summary; attempt_id and execution_status still come from the envelope."""
    rows = [bead("b")]
    e = mk_envelope("b", execution_status="missing_output", with_report=False)
    assert control.next_bead(ReadyInBdOrder(rows), unit="u", stop_at=(), run=lambda _item: 0, read_envelope=lambda _item: e) == 0
    assert capsys.readouterr().out == "b#1\tmissing_output\t-\t-\t-\n"


def test_next_no_candidate_is_prefixed_stderr_and_exit_three(capsys: pytest.CaptureFixture[str]) -> None:
    assert control.next_bead(FakeBeads(), unit=None, stop_at=(), run=lambda _item: 0, read_envelope=lambda _item: None) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "helios: no ready bead\n"


def test_next_run_raising_is_execution_failure_exit_four(capsys: pytest.CaptureFixture[str]) -> None:
    def boom(_bead: Bead) -> int:
        raise RuntimeError("kaboom")

    rows = [bead("b")]
    assert control.next_bead(ReadyInBdOrder(rows), unit="u", stop_at=(), run=boom, read_envelope=lambda item: mk_envelope(item.id)) == 4
    assert capsys.readouterr().err == "helios: execution failure for b: RuntimeError: kaboom\n"


def test_next_read_envelope_raising_is_execution_failure_exit_four(capsys: pytest.CaptureFixture[str]) -> None:
    def boom(_bead: Bead) -> Envelope:
        raise ValueError("bad json")

    rows = [bead("b")]
    assert control.next_bead(ReadyInBdOrder(rows), unit="u", stop_at=(), run=lambda _item: 0, read_envelope=boom) == 4
    assert capsys.readouterr().err == "helios: execution failure for b: ValueError: bad json\n"


def run_unit(
    rows: list[Bead],
    *,
    default: str = "auto",
    configured_until: str = "verify-code",
    until: str | None = None,
    stop_at: tuple[str, ...] = (),
    run: Any = lambda _item: 0,
    read: Any = lambda item: mk_envelope(item.id, kind=item.kind),
) -> tuple[control.UnitResult, str]:
    result = control.unit_run(ReadyInBdOrder(rows), unit="u", default=default, configured_until=configured_until, stop_at=stop_at, until=until, run=run, read_envelope=read)
    return result, result.reason


def test_unit_stop_at_before_running(capsys: pytest.CaptureFixture[str]) -> None:
    called: list[str] = []
    result, reason = run_unit([bead("b", "model")], stop_at=("model",), run=lambda item: called.append(item.id) or 0)
    assert result.code == 3 and reason == "stop_at stage model" and called == []
    assert capsys.readouterr().out == "stopped: stop_at stage model\n"


def test_unit_until_after_running(capsys: pytest.CaptureFixture[str]) -> None:
    result, reason = run_unit([bead("b")], until="impl")
    assert result.code == 0 and reason == "until stage reached"
    assert "stopped: until stage reached\n" in capsys.readouterr().out


def test_unit_stops_on_non_done_status_for_impl(capsys: pytest.CaptureFixture[str]) -> None:
    result, reason = run_unit([bead("b")], read=lambda item: mk_envelope(item.id, status="partial"))
    assert result.code == 0 and reason == "bead b report status partial"
    assert capsys.readouterr().out.endswith("stopped: bead b report status partial\n")


def test_unit_stops_on_non_verified_verdict_for_verify_kind(capsys: pytest.CaptureFixture[str]) -> None:
    result, reason = run_unit([bead("b", "verify-code")], read=lambda item: mk_envelope(item.id, kind="verify-code", verdict="inconclusive"))
    assert result.code == 0 and reason == "bead b verdict inconclusive"
    assert capsys.readouterr().out.endswith("stopped: bead b verdict inconclusive\n")


def test_unit_verify_kind_ignores_report_status_and_checks_verdict_only(capsys: pytest.CaptureFixture[str]) -> None:
    """Decided: a verify bead whose report status is 'blocked' but whose findings are
    verified continues (verify kinds are never judged on report status)."""
    e = mk_envelope("b", kind="verify-code", status="blocked", verdict="verified")
    result, reason = run_unit([bead("b", "verify-code")], read=lambda _item: e, until="verify-code")
    assert (result.code, reason) == (0, "until stage reached")


def test_unit_stops_on_nonzero_run_code_with_a_done_report(capsys: pytest.CaptureFixture[str]) -> None:
    result, reason = run_unit([bead("b")], run=lambda _item: 5)
    assert result.code == 5 and reason == "bead b run exited 5"
    assert capsys.readouterr().out.endswith("stopped: bead b run exited 5\n")


def test_unit_stops_when_no_candidate_remains(capsys: pytest.CaptureFixture[str]) -> None:
    rows = [bead("b")]
    result, reason = run_unit(rows, run=lambda _item: rows.clear() or 0)
    assert result.code == 3 and reason == "no ready bead"
    assert capsys.readouterr().out.endswith("stopped: no ready bead\n")


def test_unit_loop_guard(capsys: pytest.CaptureFixture[str]) -> None:
    result, reason = run_unit([bead("b")], configured_until="verify-code")
    assert result.code == 0 and reason == "bead b still ready after its run"
    assert capsys.readouterr().out.endswith("stopped: bead b still ready after its run\n")


def test_unit_control_defaults_and_value_errors() -> None:
    with pytest.raises(control.ControlError, match="manual control requires --until"):
        run_unit([bead("b")], default="manual")
    assert run_unit([bead("b")], default="until", configured_until="impl")[0].code == 0
    assert run_unit([bead("b")], default="auto", until="impl")[0].code == 0
    with pytest.raises(control.ControlError):
        run_unit([bead("b")], default="wat")
    with pytest.raises(control.ControlError):
        run_unit([bead("b")], until="wat")
    with pytest.raises(control.ControlError):
        run_unit([bead("b")], default="until", configured_until="wat")
    with pytest.raises(control.ControlError):
        run_unit([bead("b")], until="model")


def test_unit_verify_kind_with_no_finding_reads_dash_verdict(capsys: pytest.CaptureFixture[str]) -> None:
    result, reason = run_unit([bead("b", "verify-code")], read=lambda item: mk_envelope(item.id, kind="verify-code"))
    assert result.code == 0 and reason == "bead b verdict -"
    assert capsys.readouterr().out.endswith("stopped: bead b verdict -\n")


def test_unit_kind_outside_impl_validate_verify_skips_status_and_verdict_checks(capsys: pytest.CaptureFixture[str]) -> None:
    """Decided: frame, survey, model, report and remember are judged on neither report
    status nor verdict."""
    e = mk_envelope("m", kind="model", status="blocked")
    result, reason = run_unit([bead("m", "model")], read=lambda _item: e, until="model")
    assert (result.code, reason) == (0, "until stage reached")


def test_unit_execution_failure_no_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    result, reason = run_unit([bead("b")], read=lambda _item: None)
    assert (result.code, reason) == (4, "execution failure for b: missing envelope")
    assert capsys.readouterr().out.endswith("stopped: execution failure for b: missing envelope\n")


def test_unit_execution_failure_crashed_with_no_report(capsys: pytest.CaptureFixture[str]) -> None:
    e = mk_envelope("b", execution_status="crashed", with_report=False)
    result, reason = run_unit([bead("b")], run=lambda _item: 4, read=lambda _item: e)
    assert (result.code, reason) == (4, "execution failure for b: crashed")
    out = capsys.readouterr().out
    assert out.startswith("b#1\tcrashed\t-\t-\t-\n")
    assert out.endswith("stopped: execution failure for b: crashed\n")


def test_unit_execution_failure_interrupted_even_with_run_code_zero(capsys: pytest.CaptureFixture[str]) -> None:
    e = mk_envelope("b", execution_status="interrupted", with_report=False)
    result, reason = run_unit([bead("b")], run=lambda _item: 0, read=lambda _item: e, until="impl")
    assert (result.code, reason) == (4, "execution failure for b: interrupted")


def test_unit_run_raising_is_execution_failure_exit_four(capsys: pytest.CaptureFixture[str]) -> None:
    def boom(_bead: Bead) -> int:
        raise RuntimeError("kaboom")

    result, reason = run_unit([bead("b")], run=boom)
    assert (result.code, reason) == (4, "execution failure for b: RuntimeError: kaboom")
    assert capsys.readouterr().out.endswith("stopped: execution failure for b: RuntimeError: kaboom\n")


def test_unit_read_envelope_raising_is_execution_failure_exit_four(capsys: pytest.CaptureFixture[str]) -> None:
    def boom(_bead: Bead) -> Envelope:
        raise ValueError("bad json")

    result, reason = run_unit([bead("b")], read=boom)
    assert (result.code, reason) == (4, "execution failure for b: ValueError: bad json")


def test_unit_until_reached_only_after_an_earlier_candidate(capsys: pytest.CaptureFixture[str]) -> None:
    rows = [bead("i", "impl"), bead("v", "verify-code")]
    envelopes = {
        "i": mk_envelope("i", kind="impl"),
        "v": mk_envelope("v", kind="verify-code", verdict="verified"),
    }

    def run(item: Bead) -> int:
        rows.remove(item)
        return 0

    result, reason = run_unit(rows, run=run, read=lambda item: envelopes[item.id], until="verify-code")
    assert (result.code, reason) == (0, "until stage reached")


def config_stub(*, default: str = "auto", until: str = "verify-code", stop_at: tuple[str, ...] = ()) -> Any:
    from helios.config import Config, ControlConfig

    return Config(hub=Path("."), control=ControlConfig(default=default, until=until, stop_at=stop_at))


def test_next_command_config_error_is_prefixed_and_exit_two(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from helios.commands import next as command

    def bad_load(_path: Path) -> Any:
        raise ValueError("bad config key 'x'")

    monkeypatch.setattr(command, "load", bad_load)
    assert command.run(argparse.Namespace(unit=None)) == 2
    assert capsys.readouterr().err == "helios: bad config key 'x'\n"


def test_next_command_passes_through_run_code(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from helios.commands import next as command

    rows = [bead("b")]
    fake = ReadyInBdOrder(rows)
    monkeypatch.setattr(command, "load", lambda _path: config_stub())
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)
    monkeypatch.setattr(command, "run_bead", lambda _item: 7)
    monkeypatch.setattr(command, "read_envelope", lambda item: mk_envelope(item.id))
    assert command.run(argparse.Namespace(unit=None)) == 7
    assert capsys.readouterr().out == "b#1\tcompleted\tdone\t-\tok\n"


def test_unit_run_command_config_error_is_prefixed_and_exit_two(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from helios.commands import unit_run as command

    def bad_load(_path: Path) -> Any:
        raise TypeError("config key 'control.default' has the wrong type: 1")

    monkeypatch.setattr(command, "load", bad_load)
    assert command.run(argparse.Namespace(unit="u", until=None)) == 2
    assert capsys.readouterr().err == "helios: config key 'control.default' has the wrong type: 1\n"


def test_unit_run_command_control_error_is_prefixed_and_exit_two(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from helios.commands import unit_run as command

    monkeypatch.setattr(command, "load", lambda _path: config_stub(default="manual"))
    monkeypatch.setattr(command, "Beads", lambda _hub: ReadyInBdOrder([bead("b")]))
    assert command.run(argparse.Namespace(unit="u", until=None)) == 2
    assert capsys.readouterr().err == "helios: manual control requires --until\n"


def test_unit_run_command_passes_through_result_code(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from helios.commands import unit_run as command

    monkeypatch.setattr(command, "load", lambda _path: config_stub())
    monkeypatch.setattr(command, "Beads", lambda _hub: ReadyInBdOrder([bead("b")]))
    monkeypatch.setattr(command, "run_bead", lambda _item: 0)
    monkeypatch.setattr(command, "read_envelope", lambda item: mk_envelope(item.id))
    assert command.run(argparse.Namespace(unit="u", until="impl")) == 0
    assert capsys.readouterr().out.endswith("stopped: until stage reached\n")


# ---- item 7: real run_bead/read_envelope wiring through helios.run and helios.attempt ----


class FakeHeliosRun:
    """Stands in for the not-yet-merged helios.run module (hel-6ks)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.code = 0

    def run_many(self, bead_ids: list[str], *, hub: Path, beads: Any, config: Any) -> int:
        self.calls.append(list(bead_ids))
        return self.code


def _write_envelope(path: Path, envelope: Envelope) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(envelope.model_dump_json())


def test_next_command_run_bead_and_read_envelope_wire_to_run_many_and_attempt_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from helios.commands import next as command

    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "workflow.toml").write_text("")
    monkeypatch.chdir(tmp_path)

    fake_module = FakeHeliosRun()
    monkeypatch.setitem(sys.modules, "helios.run", fake_module)
    importlib.invalidate_caches()

    envelope = mk_envelope("b", attempt=1)
    _write_envelope(tmp_path / ".helios" / "runs" / "b" / "attempt-1" / "envelope.json", envelope)

    fake = ReadyInBdOrder([bead("b")])
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)

    assert command.run(argparse.Namespace(unit=None)) == 0
    assert fake_module.calls == [["b"]]
    assert capsys.readouterr().out == "b#1\tcompleted\tdone\t-\tok\n"


def test_next_command_run_bead_missing_helios_run_module_is_execution_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from helios.commands import next as command

    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "workflow.toml").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delitem(sys.modules, "helios.run", raising=False)

    fake = ReadyInBdOrder([bead("b")])
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)

    assert command.run(argparse.Namespace(unit=None)) == 4
    err = capsys.readouterr().err
    assert err.startswith("helios: execution failure for b: ModuleNotFoundError")


def test_unit_run_command_read_envelope_none_when_no_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from helios.commands import unit_run as command

    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "workflow.toml").write_text('[control]\ndefault = "auto"\n')
    monkeypatch.chdir(tmp_path)

    fake_module = FakeHeliosRun()
    monkeypatch.setitem(sys.modules, "helios.run", fake_module)
    importlib.invalidate_caches()

    fake = ReadyInBdOrder([bead("b")])
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)

    result_code = command.run(argparse.Namespace(unit="u", until=None))
    assert result_code == 4
    out = capsys.readouterr().out
    assert out.endswith("stopped: execution failure for b: missing envelope\n")


def test_unit_run_command_read_envelope_uses_highest_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from helios.commands import unit_run as command

    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "workflow.toml").write_text('[control]\ndefault = "auto"\n')
    monkeypatch.chdir(tmp_path)

    fake_module = FakeHeliosRun()
    monkeypatch.setitem(sys.modules, "helios.run", fake_module)
    importlib.invalidate_caches()

    _write_envelope(tmp_path / ".helios" / "runs" / "b" / "attempt-1" / "envelope.json", mk_envelope("b", attempt=1, status="partial"))
    _write_envelope(tmp_path / ".helios" / "runs" / "b" / "attempt-2" / "envelope.json", mk_envelope("b", attempt=2))

    rows = [bead("b")]
    fake = ReadyInBdOrder(rows)
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)

    result_code = command.run(argparse.Namespace(unit="u", until="impl"))
    assert result_code == 0
    out = capsys.readouterr().out
    assert out.startswith("b#2\tcompleted\tdone\t-\tok\n")
