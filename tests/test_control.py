from __future__ import annotations

from typing import Any

import pytest

from helios import control
from helios.beads import Bead, FakeBeads


def envelope(bead: str, *, status: str | None = "done", verdict: str | None = None, summary: str | None = "ok") -> dict:
    return {
        "attempt_id": f"{bead}#1",
        "execution_status": "completed",
        "report": {"status": status, "verdict": verdict, "summary": summary},
    }


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
    result = control.next_bead(ReadyInBdOrder(rows), unit="u", stop_at=("model",), run=lambda item: calls.append(item.id) or 7, read_envelope=lambda item: envelope(item.id))
    assert result == 7
    assert calls == ["run"]
    assert capsys.readouterr().out == "run#1\tcompleted\tdone\t-\tok\n"


def test_next_envelope_line_uses_dashes_for_missing_fields(capsys: pytest.CaptureFixture[str]) -> None:
    rows = [bead("b")]
    assert control.next_bead(ReadyInBdOrder(rows), unit="u", stop_at=(), run=lambda _item: 0, read_envelope=lambda _item: envelope("b", status=None, summary=None)) == 0
    assert capsys.readouterr().out == "b#1\tcompleted\t-\t-\t-\n"


def test_next_no_candidate_is_prefixed_stderr_and_exit_three(capsys: pytest.CaptureFixture[str]) -> None:
    assert control.next_bead(FakeBeads(), unit=None, stop_at=(), run=lambda _item: 0, read_envelope=lambda _item: {}) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "helios: no ready bead\n"


def run_unit(rows: list[Bead], *, default: str = "auto", configured_until: str = "verify-code", until: str | None = None, stop_at: tuple[str, ...] = (), run=lambda _item: 0, read=lambda item: envelope(item.id), **kwargs: Any) -> tuple[control.UnitResult, str]:
    default = kwargs.get("default", default)
    configured_until = kwargs.get("configured_until", configured_until)
    until = kwargs.get("until", until)
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


def test_unit_stops_on_non_done_status(capsys: pytest.CaptureFixture[str]) -> None:
    result, reason = run_unit([bead("b")], read=lambda item: envelope(item.id, status="partial"))
    assert result.code == 0 and reason == "bead b report status partial"
    assert capsys.readouterr().out.endswith("stopped: bead b report status partial\n")


def test_unit_stops_on_non_verified_verdict(capsys: pytest.CaptureFixture[str]) -> None:
    result, reason = run_unit([bead("b", "verify-code")], read=lambda item: envelope(item.id, verdict="inconclusive"))
    assert result.code == 0 and reason == "bead b verdict inconclusive"
    assert capsys.readouterr().out.endswith("stopped: bead b verdict inconclusive\n")


def test_unit_stops_on_execution_failure(capsys: pytest.CaptureFixture[str]) -> None:
    result, reason = run_unit([bead("b")], run=lambda _item: 5)
    assert result.code == 5 and reason == "execution failure for b"
    assert capsys.readouterr().out.endswith("stopped: execution failure for b\n")


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
