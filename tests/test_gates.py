from helios.beads import Bead, FakeBeads
from helios.gates import open_gates


def test_open_gates_include_blocked_beads() -> None:
    beads = FakeBeads([Bead("g", status="open", metadata={"issue_type": "gate"}), Bead("b")])
    beads.dep_add("b", "g")
    assert open_gates(beads) == [{"id": "g", "issue_type": "gate", "status": "open", "blocks": ["b"]}]
