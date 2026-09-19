"""The command layer must forward the hub's configured stage set (hel-cm9).

``control.next_bead``, ``control.unit_run`` and ``helios.learned``'s ``list_lines``/``mark``
default their ``stages`` keyword to the shipped research set. Before this bead, the command
modules that call them (``next``, ``unit run``, ``learned``) passed nothing, so a hub declaring
its own ``[[stage]]`` entries was silently selected and judged against the research stages
instead. Each test here drives the CLI against a hub whose workflow.toml declares a single stage,
``task``, that names no research stage id; the research default set contains no ``task`` id, so
if a command stopped forwarding ``config.stages`` these tests would fail exactly as described:
candidate selection would find nothing, ``--until task`` would be refused as unknown, and the
learned queue would drop the bead's marker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helios import cli, control
from helios.beads import Bead, FakeBeads
from helios.commands import learned as learned_cmd
from helios.commands import next as next_cmd
from helios.commands import unit_run as unit_run_cmd
from helios.envelope import AgentReport, Envelope

STAGE_TOML = (
    '[[stage]]\n'
    'id = "task"\n'
    'author = "implement"\n'
    'gate = "report"\n'
    'ownership = "none"\n'
)


def write_hub(tmp_path: Path) -> Path:
    """A hub declaring one stage, ``task``, outside the research set (SPEC section 3.1)."""
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "workflow.toml").write_text(STAGE_TOML)
    return tmp_path


def mk_envelope(bead_id: str, *, kind: str) -> Envelope:
    report = AgentReport(status="done", summary="ok", findings=[])  # type: ignore[arg-type]
    return Envelope(
        task_id=bead_id,
        attempt=1,
        attempt_id=f"{bead_id}#1",
        kind=kind,
        harness="fake",
        started_at="2026-01-01T00:00:00Z",
        base_commit="0" * 40,
        input_hashes={},
        execution_status="completed",  # type: ignore[arg-type]
        report=report,
    )


def stub_execution(
    monkeypatch: pytest.MonkeyPatch, module: object, fake_beads: FakeBeads, envelope: Envelope
) -> list[str]:
    """Stub the run/read_envelope/attempt_state/Beads a command module wires into control, so a
    found candidate can finish without a real harness. What is under test is whether the command
    forwards ``config.stages`` to ``control``, not the run pipeline itself.

    ``attempt_state`` must report a new attempt number after the run (Decided, control.py's
    ``_execute``), else a genuine wiring success still reads as its own execution failure.
    """
    calls: list[str] = []
    monkeypatch.setattr(module, "Beads", lambda cwd: fake_beads)
    monkeypatch.setattr(module, "run_bead", lambda bead: calls.append(bead.id) or 0)
    monkeypatch.setattr(module, "read_envelope", lambda bead: envelope)
    states = iter([control.AttemptState(0, False), control.AttemptState(1, True)])
    monkeypatch.setattr(module, "attempt_state", lambda bead: next(states))
    return calls


def test_next_selects_a_bead_whose_kind_is_the_declared_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``next`` calls ``control.candidates`` through ``control.next_bead``. The research set has
    no ``task`` id, so an unforwarded ``config.stages`` filters the bead out and `next` reports
    no ready bead (exit 3) without ever calling ``run_bead``.
    """
    hub = write_hub(tmp_path)
    monkeypatch.chdir(hub)
    fake_beads = FakeBeads([Bead("b1", kind="task", status="open", labels=["kind:task"])])
    calls = stub_execution(monkeypatch, next_cmd, fake_beads, mk_envelope("b1", kind="task"))

    code = cli.main(["next"])

    assert code == 0
    assert calls == ["b1"]


def test_unit_run_until_the_declared_stage_stops_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--until task`` is validated against the stage set ``control.unit_run`` receives
    (``validate_until``), and the loop's stop check reads that stage's ``gate`` to judge the
    run (SPEC section 11). The research set declares no ``task`` id, so an unforwarded
    ``config.stages`` refuses with "unknown until stage" (exit 2) before any bead runs.
    """
    hub = write_hub(tmp_path)
    monkeypatch.chdir(hub)
    fake_beads = FakeBeads(
        [Bead("b1", kind="task", status="open", labels=["kind:task", "unit:u1"])]
    )
    calls = stub_execution(monkeypatch, unit_run_cmd, fake_beads, mk_envelope("b1", kind="task"))

    code = cli.main(["unit", "run", "u1", "--until", "task"])

    assert code == 0
    assert calls == ["b1"]


def test_learned_lists_a_marker_from_a_declared_stage_bead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``learned`` filters to helios-kind beads (``_helios_beads``: ``bead.kind in stages``)
    before scanning comments for markers. The research set has no ``task`` id, so an
    unforwarded ``config.stages`` would silently drop this bead and its marker.
    """
    hub = write_hub(tmp_path)
    monkeypatch.chdir(hub)
    fake_beads = FakeBeads([Bead("b1", kind="task", status="open", labels=["kind:task"])])
    fake_beads.add_comment("b1", "learned: [b1#1#1] use the declared stage")
    monkeypatch.setattr(learned_cmd, "Beads", lambda cwd: fake_beads)

    code = cli.main(["learned"])

    assert code == 0
    assert "use the declared stage" in capsys.readouterr().out
