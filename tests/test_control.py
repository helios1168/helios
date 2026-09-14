from __future__ import annotations

from helios.beads import Bead, FakeBeads
from helios.control import candidates, envelope_line, next_bead, unit_run


def env(bead: str, status: str = "done", verdict: str | None = None) -> dict:
    return {"attempt_id": f"{bead}#1", "execution_status": "completed", "report": {"status": status, "verdict": verdict, "summary": "ok"}}


def test_candidates_require_stage_label_and_preserve_bd_order() -> None:
    beads = FakeBeads([
        Bead("other", kind="impl", labels=["kind:impl"]),
        Bead("wrong", kind="impl", labels=["kind:impl", "unit:y"]),
        Bead("yes", kind="impl", labels=["kind:impl", "unit:x"]),
    ])
    assert [b.id for b in candidates(beads, "x")] == ["yes"]


def test_next_prints_exact_envelope_line(capsys) -> None:
    beads = FakeBeads([Bead("b", kind="impl", labels=["kind:impl"])])
    assert next_bead(beads, unit=None, stop_at=(), run=lambda _b: 7, read_envelope=lambda _b: env("b")) == 7
    assert capsys.readouterr().out == "b#1\tcompleted\tdone\t-\tok\n"


def test_next_no_candidate_is_stderr_and_three(capsys) -> None:
    assert next_bead(FakeBeads(), unit=None, stop_at=(), run=lambda _b: 0, read_envelope=lambda _b: {}) == 3
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "no ready bead\n"


def test_unit_run_stops_at_until() -> None:
    bead = Bead("b", kind="impl", labels=["kind:impl", "unit:x"])
    result = unit_run(FakeBeads([bead]), unit="x", default="auto", configured_until="impl", stop_at=(), until="impl", run=lambda _b: 0, read_envelope=lambda _b: env("b"))
    assert result == result.__class__(0, "until stage reached")
    assert envelope_line(env("b")) == "b#1\tcompleted\tdone\t-\tok"
