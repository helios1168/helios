from __future__ import annotations

import json
import shutil
import subprocess
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest

from helios.beads import Bead, FakeBeads
from helios.gates import GateError, open_gates


def test_open_gates_include_blocked_beads_and_empty_is_empty() -> None:
    beads = FakeBeads([Bead("g", status="open", metadata={"issue_type": "gate"}), Bead("b")])
    beads.dep_add("b", "g")
    assert open_gates(beads) == [{"id": "g", "issue_type": "gate", "status": "open", "blocks": ["b"]}]
    assert open_gates(FakeBeads()) == []


def test_gate_command_text_and_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from helios.commands import gate as command

    fake = FakeBeads([Bead("g", status="open", metadata={"issue_type": "gate"}), Bead("b")])
    fake.dep_add("b", "g")
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)
    monkeypatch.setattr(command, "load", lambda _path: Namespace(hub=Path(".")))
    assert command.run(Namespace(json=False)) == 0
    assert capsys.readouterr().out == "g: b\n"
    assert command.run(Namespace(json=True)) == 0
    assert json.loads(capsys.readouterr().out) == [{"id": "g", "issue_type": "gate", "status": "open", "blocks": ["b"]}]


class WeirdGates(FakeBeads):
    def __init__(self, gates: Any) -> None:
        super().__init__()
        self._gates = gates

    def gate_list(self) -> Any:
        return self._gates

    def gate_blocks(self, gate_id: str) -> list[str]:
        return []


def test_open_gates_raises_gate_error_for_entry_without_string_id() -> None:
    with pytest.raises(GateError):
        open_gates(WeirdGates([{"title": "no id"}]))


def test_open_gates_raises_gate_error_when_gate_list_is_not_a_list() -> None:
    with pytest.raises(GateError):
        open_gates(WeirdGates({"id": "g"}))  # type: ignore[arg-type]


def test_gate_command_no_blocks_has_no_trailing_space(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Decided: a gate that blocks nothing prints 'g2:' with no trailing space."""
    from helios.commands import gate as command

    fake = FakeBeads([Bead("g2", status="open", metadata={"issue_type": "gate"})])
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)
    monkeypatch.setattr(command, "load", lambda _path: Namespace(hub=Path(".")))
    assert command.run(Namespace(json=False)) == 0
    assert capsys.readouterr().out == "g2:\n"


class FailingGateList(FakeBeads):
    def gate_list(self) -> Any:
        raise RuntimeError("bd gate list failed: boom")


def test_gate_command_bd_runtime_error_is_prefixed_and_exit_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Decided: a bd RuntimeError in `gate` prints `helios: <message>` and exits 1,
    never a traceback."""
    from helios.commands import gate as command

    monkeypatch.setattr(command, "Beads", lambda _hub: FailingGateList())
    monkeypatch.setattr(command, "load", lambda _path: Namespace(hub=Path(".")))
    assert command.run(Namespace(json=False)) == 1
    assert capsys.readouterr().err == "helios: bd gate list failed: boom\n"


def test_gate_command_unexpected_bd_output_exits_one_distinct_from_config_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Decided: unexpected bd gate output is its own exit code (1), never the exit 2
    reserved for a helios.config loading error."""
    from helios.commands import gate as command

    monkeypatch.setattr(command, "Beads", lambda _hub: WeirdGates([{"title": "no id"}]))
    monkeypatch.setattr(command, "load", lambda _path: Namespace(hub=Path(".")))
    assert command.run(Namespace(json=False)) == 1
    assert capsys.readouterr().err == "helios: unexpected bd gate output\n"


def test_gate_command_config_error_is_exit_two(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from helios.commands import gate as command

    def bad_load(_path: Path) -> Any:
        raise ValueError("bad config key 'x'")

    monkeypatch.setattr(command, "load", bad_load)
    assert command.run(Namespace(json=False)) == 2
    assert capsys.readouterr().err == "helios: bad config key 'x'\n"


BD = shutil.which("bd")


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_gate_blocks(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["bd", "init", "--non-interactive", "--prefix", "t", "--skip-agents", "--quiet"], cwd=tmp_path, check=True, capture_output=True)
    a = subprocess.run(["bd", "create", "--title", "a", "--silent"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()
    b = subprocess.run(["bd", "create", "--title", "b", "--silent"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()
    gate = json.loads(subprocess.run(["bd", "gate", "create", "--blocks", a, "--reason", "review", "--json"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout)["id"]
    subprocess.run(["bd", "dep", "add", b, gate], cwd=tmp_path, check=True, capture_output=True)
    from helios.beads import Beads
    actual = open_gates(Beads(tmp_path))
    assert len(actual) == 1
    assert actual[0]["id"] == gate
    assert actual[0]["blocks"] == sorted([a, b])
