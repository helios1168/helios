from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from helios import control, stageset
from helios.beads import Bead, FakeBeads
from helios.envelope import AgentReport, Envelope, Finding
from helios.stageset import StageSet, StageSpec

#: Every test here exercises research stage behavior unless it builds its own StageSet,
#: so this is what a direct call passes now that control's functions take no default.
RESEARCH = stageset.research()


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
    assert [item.id for item in control.candidates(ReadyInBdOrder(rows), "u", stages=RESEARCH)] == ["second", "first"]


def test_next_skips_stop_at_and_passes_run_code(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[str] = []
    rows = [bead("manual", "model"), bead("run", "impl")]
    result = control.next_bead(ReadyInBdOrder(rows), unit="u", stop_at=("model",), run=lambda item: calls.append(item.id) or 7, read_envelope=lambda item: mk_envelope(item.id), stages=RESEARCH)
    assert result == 7
    assert calls == ["run"]
    assert capsys.readouterr().out == "run#1\tcompleted\tdone\t-\tok\n"


def test_next_line_verdict_is_overall_verdict_of_findings(capsys: pytest.CaptureFixture[str]) -> None:
    """SPEC section 4.2/11: the verdict column is overall_verdict(findings), never a
    ``report["verdict"]`` field, which AgentReport does not have."""
    rows = [bead("v", "verify-code")]
    result = control.next_bead(ReadyInBdOrder(rows), unit="u", stop_at=(), run=lambda _item: 0, read_envelope=lambda item: mk_envelope(item.id, kind="verify-code", verdict="inconclusive"), stages=RESEARCH)
    assert result == 0
    assert capsys.readouterr().out == "v#1\tcompleted\tdone\tinconclusive\tok\n"


def test_next_envelope_line_uses_dashes_when_no_report(capsys: pytest.CaptureFixture[str]) -> None:
    """A missing report (execution failed before one was captured) shows '-' for status,
    verdict and summary; attempt_id and execution_status still come from the envelope."""
    rows = [bead("b")]
    e = mk_envelope("b", execution_status="missing_output", with_report=False)
    assert control.next_bead(ReadyInBdOrder(rows), unit="u", stop_at=(), run=lambda _item: 0, read_envelope=lambda _item: e, stages=RESEARCH) == 0
    assert capsys.readouterr().out == "b#1\tmissing_output\t-\t-\t-\n"


def test_next_missing_envelope_after_in_place_recovery_uses_run_code(capsys: pytest.CaptureFixture[str]) -> None:
    """Decided (item 3): a run that returns without raising but leaves no envelope
    (here, SPEC section 8.4 in-place recovery of an unfinalized attempt whose run
    refuses, exit 2, because the attempt is live or locked) prints the missing
    envelope reason and exits with the run's own nonzero code, leaving the attempt
    state as the fake left it (unchanged, not finalized)."""
    rows = [bead("b")]
    state = control.AttemptState(number=1, finalized=False)
    code = control.next_bead(
        ReadyInBdOrder(rows),
        unit="u",
        stop_at=(),
        run=lambda _item: 2,
        read_envelope=lambda _item: None,
        attempt_state=lambda _item: state,
        stages=RESEARCH,
    )
    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "helios: execution failure for b: missing envelope\n"


def test_next_missing_envelope_with_run_code_zero_is_exit_four(capsys: pytest.CaptureFixture[str]) -> None:
    """Decided (item 3): a missing envelope with the run's own code zero (or no
    attempt_state tracking at all) falls back to exit 4."""
    rows = [bead("b")]
    code = control.next_bead(
        ReadyInBdOrder(rows),
        unit="u",
        stop_at=(),
        run=lambda _item: 0,
        read_envelope=lambda _item: None,
        stages=RESEARCH,
    )
    assert code == 4
    assert capsys.readouterr().err == "helios: execution failure for b: missing envelope\n"


def test_next_no_candidate_is_prefixed_stderr_and_exit_three(capsys: pytest.CaptureFixture[str]) -> None:
    assert control.next_bead(FakeBeads(), unit=None, stop_at=(), run=lambda _item: 0, read_envelope=lambda _item: None, stages=RESEARCH) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "helios: no ready bead\n"


def test_next_run_raising_is_execution_failure_exit_four(capsys: pytest.CaptureFixture[str]) -> None:
    def boom(_bead: Bead) -> int:
        raise RuntimeError("kaboom")

    rows = [bead("b")]
    assert control.next_bead(ReadyInBdOrder(rows), unit="u", stop_at=(), run=boom, read_envelope=lambda item: mk_envelope(item.id), stages=RESEARCH) == 4
    assert capsys.readouterr().err == "helios: execution failure for b: RuntimeError: kaboom\n"


def test_next_read_envelope_raising_is_execution_failure_exit_four(capsys: pytest.CaptureFixture[str]) -> None:
    def boom(_bead: Bead) -> Envelope:
        raise ValueError("bad json")

    rows = [bead("b")]
    assert control.next_bead(ReadyInBdOrder(rows), unit="u", stop_at=(), run=lambda _item: 0, read_envelope=boom, stages=RESEARCH) == 4
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
    attempt_state: Any = None,
    stages: StageSet = RESEARCH,
) -> tuple[control.UnitResult, str]:
    result = control.unit_run(
        ReadyInBdOrder(rows),
        unit="u",
        default=default,
        configured_until=configured_until,
        stop_at=stop_at,
        until=until,
        run=run,
        read_envelope=read,
        attempt_state=attempt_state,
        stages=stages,
    )
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


def test_unit_run_with_single_declared_stage_runs_its_loop(capsys: pytest.CaptureFixture[str]) -> None:
    """A hub need not declare the research set: one stage whose gate is 'report' is
    enough for candidates, the report-status stop check and 'until' to all work
    through the configured stage set alone."""
    stages = StageSet((StageSpec(id="work", author="implement", gate="report"),))
    result = control.unit_run(
        ReadyInBdOrder([bead("b", "work")]),
        unit="u",
        default="auto",
        configured_until="work",
        stop_at=(),
        until="work",
        run=lambda _item: 0,
        read_envelope=lambda item: mk_envelope(item.id, kind="work"),
        stages=stages,
    )
    assert (result.code, result.reason) == (0, "until stage reached")
    assert capsys.readouterr().out.endswith("stopped: until stage reached\n")


def test_validate_until_unknown_stage_names_declared_ids() -> None:
    """SPEC section 11: a refusal for an undeclared stage names the declared ids."""
    stages = StageSet((StageSpec(id="work", author="implement", gate="report"),))
    with pytest.raises(control.ControlError, match=r"unknown until stage nope; declared: work"):
        control.validate_until(FakeBeads(), "u", "nope", stages=stages)


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


def test_unit_execution_failure_exit_uses_run_code_when_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    """Decided (item 2): an execution failure exits with the run's own code when it is
    nonzero (a preflight refusal, exit 2), not the hardcoded 4."""
    e = mk_envelope("b", execution_status="crashed", with_report=False)
    result, reason = run_unit([bead("b")], run=lambda _item: 2, read=lambda _item: e)
    assert (result.code, reason) == (2, "execution failure for b: crashed")


def test_unit_no_attempt_before_or_after_is_execution_failure(capsys: pytest.CaptureFixture[str]) -> None:
    """Decided (item 3, revised): still no attempt at all (number 0 before and after)
    is its own execution failure, reason 'no new attempt'; exit 4 since the run's own
    code is 0."""
    result, reason = run_unit(
        [bead("b")],
        run=lambda _item: 0,
        read=lambda item: mk_envelope(item.id),
        attempt_state=lambda _item: control.AttemptState(number=0, finalized=False),
    )
    assert (result.code, reason) == (4, "execution failure for b: no new attempt")
    assert capsys.readouterr().out.endswith("stopped: execution failure for b: no new attempt\n")


def test_unit_unchanged_finalized_attempt_is_execution_failure_using_run_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Decided (item 3, revised): an unchanged attempt number is still an execution
    failure when that attempt was already finalized before the run, exit with the
    run's own nonzero code."""
    result, reason = run_unit(
        [bead("b")],
        run=lambda _item: 2,
        read=lambda item: mk_envelope(item.id),
        attempt_state=lambda _item: control.AttemptState(number=5, finalized=True),
    )
    assert (result.code, reason) == (2, "execution failure for b: no new attempt")
    assert capsys.readouterr().out.endswith("stopped: execution failure for b: no new attempt\n")


def test_unit_new_attempt_number_is_read_normally(capsys: pytest.CaptureFixture[str]) -> None:
    """Decided (item 3, revised): a higher attempt number after the run is a genuinely
    new attempt, read normally, not a failure."""
    states = iter([control.AttemptState(number=0, finalized=False), control.AttemptState(number=1, finalized=False)])
    result, reason = run_unit(
        [bead("b")],
        run=lambda _item: 0,
        read=lambda item: mk_envelope(item.id),
        attempt_state=lambda _item: next(states),
        until="impl",
    )
    assert (result.code, reason) == (0, "until stage reached")


def test_unit_in_place_recovery_of_unfinalized_attempt_reads_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    """Decided (item 3, revised): an unchanged attempt number is not a failure when
    that attempt was not yet finalized before the run (SPEC section 8.4 recovery of
    native_completed, validated or invalid finalizes the same attempt in place). The
    envelope the fake run wrote in place is read normally."""
    state = control.AttemptState(number=1, finalized=False)
    result, reason = run_unit(
        [bead("b")],
        run=lambda _item: 0,
        read=lambda item: mk_envelope(item.id, attempt=1),
        attempt_state=lambda _item: state,  # same object before and after: unchanged, not finalized
        until="impl",
    )
    assert (result.code, reason) == (0, "until stage reached")


def test_next_unchanged_finalized_attempt_is_execution_failure_using_run_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [bead("b")]
    code = control.next_bead(
        ReadyInBdOrder(rows),
        unit="u",
        stop_at=(),
        run=lambda _item: 2,
        read_envelope=lambda item: mk_envelope(item.id),
        attempt_state=lambda _item: control.AttemptState(number=5, finalized=True),
        stages=RESEARCH,
    )
    assert code == 2
    assert capsys.readouterr().err == "helios: execution failure for b: no new attempt\n"


def test_next_new_attempt_number_is_read_normally(capsys: pytest.CaptureFixture[str]) -> None:
    states = iter([control.AttemptState(number=0, finalized=False), control.AttemptState(number=1, finalized=False)])
    rows = [bead("b")]
    code = control.next_bead(
        ReadyInBdOrder(rows),
        unit="u",
        stop_at=(),
        run=lambda _item: 0,
        read_envelope=lambda item: mk_envelope(item.id),
        attempt_state=lambda _item: next(states),
        stages=RESEARCH,
    )
    assert code == 0
    assert capsys.readouterr().out == "b#1\tcompleted\tdone\t-\tok\n"


def test_next_in_place_recovery_of_unfinalized_attempt_reads_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    state = control.AttemptState(number=1, finalized=False)
    rows = [bead("b")]
    code = control.next_bead(
        ReadyInBdOrder(rows),
        unit="u",
        stop_at=(),
        run=lambda _item: 0,
        read_envelope=lambda item: mk_envelope(item.id, attempt=1),
        attempt_state=lambda _item: state,
        stages=RESEARCH,
    )
    assert code == 0
    assert capsys.readouterr().out == "b#1\tcompleted\tdone\t-\tok\n"


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


def _incrementing_attempt() -> Any:
    """An attempt_state stub whose number differs on the after-call (a new attempt was
    made), so tests unrelated to item 3 are not tripped up by that check."""
    counter = iter(range(1_000_000))
    return lambda _item: control.AttemptState(number=next(counter), finalized=False)


def test_next_command_passes_through_run_code(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from helios.commands import next as command

    rows = [bead("b")]
    fake = ReadyInBdOrder(rows)
    monkeypatch.setattr(command, "load", lambda _path: config_stub())
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)
    monkeypatch.setattr(command, "run_bead", lambda _item: 7)
    monkeypatch.setattr(command, "read_envelope", lambda item: mk_envelope(item.id))
    monkeypatch.setattr(command, "attempt_state", _incrementing_attempt())
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
    monkeypatch.setattr(command, "attempt_state", _incrementing_attempt())
    assert command.run(argparse.Namespace(unit="u", until="impl")) == 0
    assert capsys.readouterr().out.endswith("stopped: until stage reached\n")


# ---- item 7: real run_bead/read_envelope wiring through helios.run and helios.attempt ----


class FakeHeliosRun:
    """Stands in for the not-yet-merged helios.run module (hel-6ks)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.code = 0

    def run_many(self, bead_ids: list[str], *, hub: Path, beads: Any, config: Any, memory_has: Any = None) -> int:
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

    envelope = mk_envelope("b", attempt=1)

    class WritesAttempt(FakeHeliosRun):
        def run_many(self, bead_ids: list[str], *, hub: Path, beads: Any, config: Any, memory_has: Any = None) -> int:
            super().run_many(bead_ids, hub=hub, beads=beads, config=config, memory_has=memory_has)
            _write_envelope(tmp_path / ".helios" / "runs" / "b" / "attempt-1" / "envelope.json", envelope)
            return self.code

    fake_module = WritesAttempt()
    monkeypatch.setitem(sys.modules, "helios.run", fake_module)
    importlib.invalidate_caches()

    fake = ReadyInBdOrder([bead("b")])
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)

    assert command.run(argparse.Namespace(unit=None)) == 0
    assert fake_module.calls == [["b"]]
    assert capsys.readouterr().out == "b#1\tcompleted\tdone\t-\tok\n"


def test_next_command_run_bead_missing_helios_run_module_is_execution_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """main now ships a real helios.run (hel-6ks), so simulating 'missing' means
    making the import itself fail, not removing it from sys.modules (which would just
    re-import the real file from disk)."""
    from helios.commands import next as command

    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "workflow.toml").write_text("")
    monkeypatch.chdir(tmp_path)

    real_import_module = importlib.import_module

    def fake_import_module(name: str, *a: Any, **kw: Any) -> Any:
        if name == "helios.run":
            raise ModuleNotFoundError("No module named 'helios.run'")
        return real_import_module(name, *a, **kw)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)

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
    assert out.endswith("stopped: execution failure for b: no new attempt\n")


def test_unit_run_command_read_envelope_uses_highest_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command reads the highest attempt written for the new run, not an older one
    already on disk (item 3: a run that leaves the attempt count unchanged is instead
    'no new attempt', so the run here must create attempt-2 to succeed)."""
    from helios.commands import unit_run as command

    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "workflow.toml").write_text('[control]\ndefault = "auto"\n')
    monkeypatch.chdir(tmp_path)

    _write_envelope(tmp_path / ".helios" / "runs" / "b" / "attempt-1" / "envelope.json", mk_envelope("b", attempt=1, status="partial"))

    class WritesNewAttempt(FakeHeliosRun):
        def run_many(self, bead_ids: list[str], *, hub: Path, beads: Any, config: Any, memory_has: Any = None) -> int:
            super().run_many(bead_ids, hub=hub, beads=beads, config=config, memory_has=memory_has)
            _write_envelope(tmp_path / ".helios" / "runs" / "b" / "attempt-2" / "envelope.json", mk_envelope("b", attempt=2))
            return self.code

    fake_module = WritesNewAttempt()
    monkeypatch.setitem(sys.modules, "helios.run", fake_module)
    importlib.invalidate_caches()

    rows = [bead("b")]
    fake = ReadyInBdOrder(rows)
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)

    result_code = command.run(argparse.Namespace(unit="u", until="impl"))
    assert result_code == 0
    out = capsys.readouterr().out
    assert out.startswith("b#2\tcompleted\tdone\t-\tok\n")


def test_attempt_state_agrees_between_next_and_unit_run_on_bad_pid_and_missing_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``attempt_state`` in both command modules now reads through
    ``attempt.normalized_state`` instead of ``attempt.read_state`` (hel-v2m item 2). Both
    read only the ``state`` field, which normalization never touches, so a ``pid`` that
    fails validation, or a missing or unreadable ``state.json``, must leave next and unit
    run agreeing exactly as before (SPEC section 8.3)."""
    from helios import attempt as att
    from helios.commands import next as next_command
    from helios.commands import unit_run as unit_run_command

    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "workflow.toml").write_text("")
    monkeypatch.chdir(tmp_path)

    b = bead("b")

    no_attempt = control.AttemptState(number=0, finalized=False)
    assert next_command.attempt_state(b) == unit_run_command.attempt_state(b) == no_attempt

    directory = tmp_path / ".helios" / "runs" / "b" / "attempt-1"
    directory.mkdir(parents=True)

    not_finalized = control.AttemptState(number=1, finalized=False)
    for bad_pid in (0, "123", True):
        att.write_state(directory, attempt_id="b#1", state="launched", pid=bad_pid)  # type: ignore[arg-type]
        assert next_command.attempt_state(b) == not_finalized
        assert unit_run_command.attempt_state(b) == not_finalized

    # An unreadable state.json falls back to allocated, not finalized (SPEC section 8.3).
    (directory / "state.json").write_text("not json")
    assert next_command.attempt_state(b) == not_finalized
    assert unit_run_command.attempt_state(b) == not_finalized

    # A missing state.json falls back the same way.
    (directory / "state.json").unlink()
    assert next_command.attempt_state(b) == not_finalized
    assert unit_run_command.attempt_state(b) == not_finalized

    # A finalized attempt still reads as finalized regardless of the pid's shape.
    att.write_state(directory, attempt_id="b#1", state="finalized", pid=True)  # type: ignore[arg-type]
    finalized = control.AttemptState(number=1, finalized=True)
    assert next_command.attempt_state(b) == finalized
    assert unit_run_command.attempt_state(b) == finalized


# ---- round 3 item 2: memory_has wired into run_many (a missing kwarg always refused) ----


def test_next_run_bead_passes_memory_has_built_from_configured_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decided (round 3, item 2): run_bead must pass memory_has to run_many; without
    it, run_many defaults to a lambda that is always False, so preflight refuses every
    bead that lists memories, present or not."""
    from helios.commands import next as command

    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "workflow.toml").write_text("")
    monkeypatch.chdir(tmp_path)

    fake = FakeBeads([bead("b")])
    fake.remember("m1", "value")
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)

    captured: dict[str, Any] = {}

    def run_many(bead_ids: list[str], *, hub: Path, beads: Any, config: Any, memory_has: Any = None) -> int:
        captured["memory_has"] = memory_has
        return 0

    monkeypatch.setitem(sys.modules, "helios.run", types.SimpleNamespace(run_many=run_many))
    importlib.invalidate_caches()

    assert command.run_bead(bead("b")) == 0
    assert captured["memory_has"] is not None
    assert captured["memory_has"]("m1") is True
    assert captured["memory_has"]("missing") is False


def _memory_hub(tmp_path: Path) -> Path:
    """A minimal git hub for a FakeBeads-driven, real helios.run.run_many call with the
    fake harness (mirrors tests/test_run_fake.py's make_hub; duplicated here to keep
    this file self-contained)."""
    hub = tmp_path / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=hub, check=True)
    (hub / "README.md").write_text("hi\n")
    (hub / ".gitignore").write_text(".helios/\n.claude/worktrees/\n")
    (hub / "skills" / "impl").mkdir(parents=True)
    (hub / "skills" / "impl" / "SKILL.md").write_text("---\nname: impl\n---\n\n# impl\n\nDo it.\n")
    (hub / "AGENTS.md").write_text("# p\n\n## Worker contract\n\nOne bead.\n")
    subprocess.run(["git", "add", "."], cwd=hub, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=hub, check=True)
    (hub / ".agents").mkdir()
    (hub / ".agents" / "workflow.toml").write_text('[agents]\nimplement = "fake"\n')
    return hub


def test_next_run_bead_with_present_memory_runs_through_real_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end regression for item 2: a bead whose memory is present in the
    configured (beads) backend must pass preflight and run, through the real
    helios.run and the fake harness."""
    from helios.commands import next as command

    hub = _memory_hub(tmp_path)
    monkeypatch.chdir(hub)
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"exit_code": 0, "sleep_s": 0, "stdout": "ok", "session_id": "s1",
                                   "report": {"status": "done", "summary": "did it"}}))
    monkeypatch.setenv("HELIOS_FAKE_SCRIPT", str(script))

    from helios import memory as memory_mod

    b1 = Bead("b1", kind="impl", files=["src/"], test="true", memories=["m1"])
    fake = FakeBeads([b1])
    # A real memory value (SPEC 13 format), not a bare string: helios.run's
    # preflight-passed-but-unreadable path now refuses rather than falling
    # back to "" (hel-dgl item 2), so the stored value must actually parse.
    fake.remember("m1", memory_mod.serialize({"source": "x#1", "status": "active"}, "value"))
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)

    assert command.run_bead(b1) == 0


def test_next_run_bead_with_missing_memory_refuses_at_preflight_exit_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A memory absent from the configured backend still gives the preflight refusal,
    exit 2, and creates no attempt."""
    from helios.commands import next as command

    hub = _memory_hub(tmp_path)
    monkeypatch.chdir(hub)
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"exit_code": 0, "sleep_s": 0, "stdout": "ok", "session_id": "s1",
                                   "report": {"status": "done", "summary": "did it"}}))
    monkeypatch.setenv("HELIOS_FAKE_SCRIPT", str(script))

    b1 = Bead("b1", kind="impl", files=["src/"], test="true", memories=["missing"])
    fake = FakeBeads([b1])
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)

    assert command.run_bead(b1) == 2
    assert not (hub / ".helios" / "runs" / "b1").exists()


# ---- round 3 item 3: a bd RuntimeError while listing candidates, not a traceback ----


class FailingReadyBeads(FakeBeads):
    def ready(self, *, labels: list[str] = []) -> list[Bead]:
        raise RuntimeError("bd ready failed: boom")


def test_next_command_bd_runtime_error_listing_candidates_is_prefixed_and_exit_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from helios.commands import next as command

    monkeypatch.setattr(command, "load", lambda _path: config_stub())
    monkeypatch.setattr(command, "Beads", lambda _hub: FailingReadyBeads())
    assert command.run(argparse.Namespace(unit=None)) == 1
    assert capsys.readouterr().err == "helios: bd ready failed: boom\n"


def test_unit_run_command_bd_runtime_error_listing_candidates_is_prefixed_and_exit_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from helios.commands import unit_run as command

    monkeypatch.setattr(command, "load", lambda _path: config_stub())
    monkeypatch.setattr(command, "Beads", lambda _hub: FailingReadyBeads())
    assert command.run(argparse.Namespace(unit="u", until=None)) == 1
    assert capsys.readouterr().err == "helios: bd ready failed: boom\n"
