from __future__ import annotations

import json
import shutil
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from helios.beads import Bead, FakeBeads
from helios.gates import open_gates


def test_open_gates_include_blocked_beads_and_empty_is_empty() -> None:
    beads = FakeBeads([Bead("g", status="open", metadata={"issue_type": "gate"}), Bead("b")])
    beads.dep_add("b", "g")
    assert open_gates(beads) == [{"id": "g", "issue_type": "gate", "status": "open", "blocks": ["b"]}]
    assert open_gates(FakeBeads()) == []


def test_gate_command_text_and_json(monkeypatch, capsys) -> None:
    from helios.commands import gate as command

    fake = FakeBeads([Bead("g", status="open", metadata={"issue_type": "gate"}), Bead("b")])
    fake.dep_add("b", "g")
    monkeypatch.setattr(command, "Beads", lambda _hub: fake)
    monkeypatch.setattr(command, "load", lambda _path: Namespace(hub=Path(".")))
    assert command.run(Namespace(json=False)) == 0
    assert capsys.readouterr().out == "g: b\n"
    assert command.run(Namespace(json=True)) == 0
    assert json.loads(capsys.readouterr().out) == [{"id": "g", "issue_type": "gate", "status": "open", "blocks": ["b"]}]


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
