"""End to end proof that a hub with no research stages is first class (SPEC section 3).

A hub declaring one stage of its own, with a skill and the fake harness, goes through
`helios run` and then `helios merge` the same way a research hub's impl bead does: the stage
id never appears in either pipeline. Setup follows tests/test_run_fake.py's `make_hub` and
tests/test_merge.py's evidence fixture rather than inventing a new one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from helios import beads as beads_mod
from helios import config as config_mod
from helios import envelope as envelope_mod
from helios import run as run_mod
from helios.merge import merge_bead


def make_hub(tmp_path: Path) -> Path:
    """A hub declaring one general purpose stage, "solo": no files or test demand, ownership
    open to any path, gated on the report like the research impl stage.
    """
    hub = tmp_path / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=hub, check=True)
    (hub / "README.md").write_text("hi\n")
    (hub / ".gitignore").write_text(".helios/\n.claude/worktrees/\n")
    (hub / "AGENTS.md").write_text(
        "# helios\n\n## Worker contract\n\nWork exactly one bead.\n\n## Orchestrator\n\nMerges.\n"
    )
    (hub / ".agents").mkdir()
    (hub / ".agents" / "workflow.toml").write_text(
        '[[stage]]\n'
        'id = "solo"\n'
        'author = "implement"\n'
        'gate = "report"\n'
        'ownership = "none"\n'
    )
    (hub / "skills" / "solo").mkdir(parents=True)
    (hub / "skills" / "solo" / "SKILL.md").write_text(
        "---\nname: solo\n---\n\n# Solo bead\n\nA general purpose stage, not a research kind.\n"
    )
    subprocess.run(["git", "add", "."], cwd=hub, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=hub, check=True)
    return hub


def write_script(tmp_path: Path, payload: dict) -> Path:
    import json

    path = tmp_path / "script.json"
    path.write_text(json.dumps(payload))
    return path


def test_general_purpose_hub_runs_and_merges(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    assert cfg.stages.ids == ("solo",)

    bead = beads_mod.Bead(
        id="b1",
        kind="solo",
        unit="gp",
        test="mkdir -p out && echo hi > out/result.txt && exit 0",
    )
    beads = beads_mod.FakeBeads([bead])
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 0, "stdout": "ok", "session_id": "s1",
         "report": {"status": "done", "summary": "did it"}},
    )
    monkeypatch.setenv("HELIOS_FAKE_SCRIPT", str(script))

    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    assert rc == 0
    assert "b1" in beads.closed
    # FakeBeads.close only records the close reason (SPEC §7.5's `bd close`); it does not flip
    # status the way a real `bd` would, so the merge precondition below sets it by hand, the
    # same way tests/test_beads.py does for a bead closed out from under a fake dependency.
    beads.beads["b1"].status = "closed"
    output_commit = beads.beads["b1"].metadata.get("output_commit")
    assert output_commit

    worktree = hub / ".claude" / "worktrees" / "b1"
    assert (worktree / "out" / "result.txt").read_text() == "hi\n"

    beads.beads["v1"] = beads_mod.Bead(
        id="v1",
        kind="solo",
        unit="gp",
        parent="b1",
        status="closed",
        metadata={"verdict": "verified", "attempt": 1},
        labels=["unit:gp"],
    )
    envelope_dir = hub / ".helios" / "runs" / "v1" / "attempt-1"
    envelope_dir.mkdir(parents=True)
    (envelope_dir / "envelope.json").write_text(
        envelope_mod.Envelope(
            task_id="v1",
            attempt=1,
            attempt_id="v1#1",
            kind="solo",
            harness="fake",
            started_at="now",
            base_commit=output_commit,
            input_hashes={},
            execution_status=envelope_mod.ExecutionStatus.COMPLETED,
            report=envelope_mod.AgentReport(status=envelope_mod.WorkStatus.DONE, summary="ok"),
        ).model_dump_json()
    )

    rc, message = merge_bead(
        hub,
        "b1",
        project=cfg.project,
        config=cfg,
        beads=beads,
        check_runner=lambda _command, _cwd: 0,
        input_hashes=lambda _bead: {},
    )
    assert rc == 0, message
    assert beads.beads["b1"].metadata.get("merge_commit")
    landed = subprocess.run(
        ["git", "show", "main:out/result.txt"], cwd=hub, check=True,
        capture_output=True, text=True,
    ).stdout
    assert landed == "hi\n"
