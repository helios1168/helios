"""``helios run`` tests for hel-dgl acceptance items 1-3, 6, 7 and 9.

1) input.json (SPEC §7.1 step 6). 2) the stop-requested marker (SPEC §4.4,
§7.1, §9.2). 3) a verify worktree starting at the parent's output_commit
(SPEC §7.1, §7.3). 6) memory injection into the prompt and input.json (SPEC
§7.2 item 5, §13). 7) the SIGINT reentry guard and the memory lookup failure
message (SPEC §7.1, round 7 fixes). 9) helios run moves a bead to
in_progress once preflight passes and before launch (SPEC §7.5, §11).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from helios import attempt as attempt_mod
from helios import beads as beads_mod
from helios import config as config_mod
from helios import envelope as envelope_mod
from helios import memory as memory_mod
from helios import run as run_mod
from helios import sessions as sessions_mod
from helios import worktree as worktree_mod


def make_hub(tmp_path: Path) -> Path:
    hub = tmp_path / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=hub, check=True)
    (hub / "README.md").write_text("hi\n")
    (hub / ".gitignore").write_text(".helios/\n.claude/worktrees/\n")
    for kind in ("impl", "verify-code"):
        (hub / "skills" / kind).mkdir(parents=True)
        (hub / "skills" / kind / "SKILL.md").write_text(
            f"---\nname: {kind}\n---\n\n# {kind} bead\n\nDo the work.\n"
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
    return beads_mod.Bead(**fields)  # type: ignore[arg-type]


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


def wait_for(path: Path, needle: str = "", limit: float = 20) -> None:
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if path.exists() and needle in path.read_text():
            return
        time.sleep(0.05)
    raise TimeoutError(str(path))


DONE_SCRIPT = {"exit_code": 0, "sleep_s": 0, "stdout": "x", "session_id": "s1",
               "report": {"status": "done", "summary": "ok"}}


# --------------------------------------------------------------- item 1: input.json


def test_input_json_has_harness_and_sorted_hashes(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    (hub / "docs").mkdir()
    (hub / "docs" / "DOC.md").write_text("# Doc\n\nSome text.\n")
    beads = beads_mod.FakeBeads([make_bead("b1", docs=["docs/DOC.md"])])
    set_fake(monkeypatch, write_script(tmp_path, DONE_SCRIPT))
    cfg = config_mod.load(hub)
    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    assert rc == 0
    input_path = hub / ".helios" / "runs" / "b1" / "attempt-1" / "input.json"
    raw = input_path.read_text()
    data = json.loads(raw)
    assert data["harness"] == "fake"
    assert set(data["input_hashes"]) == {"prompt", "bead", "docs/docs/DOC.md"}
    assert data["input_hashes"]["docs/docs/DOC.md"] == run_mod.sha256_text(
        (hub / "docs" / "DOC.md").read_text()
    )
    # sort_keys and indent 2, exactly as written (SPEC §7.1 step 6).
    assert raw == json.dumps(data, indent=2, sort_keys=True) + "\n"
    assert list(input_path.parent.glob("input.*.tmp")) == []


def test_input_json_unit_hash_present(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    (hub / "docs" / "units").mkdir(parents=True)
    (hub / "docs" / "units" / "U1.md").write_text("# U1\n\nBody.\n")
    beads = beads_mod.FakeBeads([make_bead("b1", unit="U1")])
    set_fake(monkeypatch, write_script(tmp_path, DONE_SCRIPT))
    cfg = config_mod.load(hub)
    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    assert rc == 0
    data = json.loads(
        (hub / ".helios" / "runs" / "b1" / "attempt-1" / "input.json").read_text()
    )
    assert data["input_hashes"]["unit"] == run_mod.sha256_text(
        (hub / "docs" / "units" / "U1.md").read_text()
    )


# --------------------------------------------------------- item 2: stop-requested


def test_stop_requested_during_wait_classifies_interrupted(
    tmp_path: Path, monkeypatch
) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    script = write_script(
        tmp_path,
        {"exit_code": 0, "sleep_s": 30, "stdout": "slow", "session_id": "s1",
         "report": {"status": "done", "summary": "late"}},
    )
    set_fake(monkeypatch, script)
    cfg = config_mod.load(hub)
    attempt_dir = hub / ".helios" / "runs" / "b1" / "attempt-1"

    def stopper() -> None:
        wait_for(attempt_dir / "stdout.jsonl", "slow")
        sessions_mod.stop(hub, cfg.project.runs, "b1")

    thread = threading.Thread(target=stopper)
    thread.start()
    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    thread.join()
    assert rc == 4
    env = read_envelope(hub, "b1", 1)
    assert env["execution_status"] == "interrupted"
    assert (attempt_dir / "stop-requested").exists()


def test_recovery_reads_stop_requested_as_interrupted_not_crashed(
    tmp_path: Path, monkeypatch
) -> None:
    """A stop-requested attempt that helios never got to classify recovers
    as interrupted, not crashed (SPEC §4.4 point 2, §8.4)."""
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    info = worktree_mod.prepare(hub=hub, bead="b1")
    attempt_obj, _ = attempt_mod.allocate(
        hub=hub, runs_rel=cfg.project.runs, bead="b1", worktree=info.path
    )
    (attempt_obj.dir / "stop-requested").write_text("2024-01-01T00:00:00Z\n")
    set_fake(monkeypatch, write_script(tmp_path, DONE_SCRIPT))
    beads = beads_mod.FakeBeads(
        [beads_mod.Bead(id="b1", kind="impl", files=["src/b1/"], test="true")]
    )
    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    assert rc == 0
    log = (hub / ".helios" / "runs" / "b1" / "attempt-1" / "state.log").read_text()
    order = [json.loads(line)["state"] for line in log.splitlines()]
    assert "crashed" not in order
    assert order.index("interrupted") < order.index("finalized")
    assert read_envelope(hub, "b1", 1)["execution_status"] == "interrupted"
    assert (hub / ".helios" / "runs" / "b1" / "attempt-2").exists()


# ------------------------------------------------- item 3: verify worktree start


def _commit(hub: Path, name: str) -> str:
    (hub / name).write_text("x\n")
    subprocess.run(["git", "add", name], cwd=hub, check=True)
    subprocess.run(["git", "commit", "-qm", name], cwd=hub, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=hub, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_verify_worktree_starts_at_parent_output_commit(
    tmp_path: Path, monkeypatch
) -> None:
    hub = make_hub(tmp_path)
    older = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=hub, capture_output=True, text=True, check=True
    ).stdout.strip()
    tip = _commit(hub, "extra.txt")
    assert older != tip
    parent = beads_mod.Bead(
        id="impl1", kind="impl", files=["src/"], test="true",
        metadata={"output_commit": older},
    )
    verify_bead = beads_mod.Bead(
        id="v1", kind="verify-code", unit="U1", parent="impl1",
        files=["tools/verify/U1/"], test="true",
    )
    beads = beads_mod.FakeBeads([parent, verify_bead])
    verify_report = {
        "status": "done", "summary": "ok",
        "findings": [
            {"id": "f1", "claim": "c", "verdict": "verified", "method": "review",
             "scope": "universal"}
        ],
    }
    set_fake(
        monkeypatch,
        write_script(tmp_path, {**DONE_SCRIPT, "report": verify_report}),
    )
    cfg = config_mod.load(hub)
    rc = run_mod.run_one("v1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    assert rc == 0
    env = read_envelope(hub, "v1", 1)
    assert env["base_commit"] == older
    assert env["base_commit"] != tip


def test_verify_worktree_missing_parent_output_commit_is_preflight_exit2(
    tmp_path: Path, capsys
) -> None:
    hub = make_hub(tmp_path)
    parent = beads_mod.Bead(id="impl1", kind="impl", files=["src/"], test="true")
    verify_bead = beads_mod.Bead(
        id="v1", kind="verify-code", unit="U1", parent="impl1",
        files=["tools/verify/U1/"], test="true",
    )
    beads = beads_mod.FakeBeads([parent, verify_bead])
    cfg = config_mod.load(hub)
    rc = run_mod.run_many(["v1"], hub=hub, beads=beads, config=cfg, harness_override="fake")
    err = capsys.readouterr().err
    assert rc == 2
    assert "output_commit" in err
    assert not (hub / ".claude" / "worktrees" / "v1").exists()


# ------------------------------------------------------- item 6: memory injection


def _memories_section(prompt: str) -> str:
    after = prompt.split("## Memories\n\n", 1)[1]
    return after.rsplit("\n\n## Attempt\n\n", 1)[0]


def test_memories_section_files_backend_matches_inject(
    tmp_path: Path, monkeypatch
) -> None:
    import dataclasses

    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)
    cfg = dataclasses.replace(cfg, memory=dataclasses.replace(cfg.memory, backend="files"))
    backend = memory_mod.FilesBackend(cfg.hub / cfg.memory.export_dir)
    backend.write("k1", {"source": "hel-1#1"}, "hello world")
    beads = beads_mod.FakeBeads([make_bead("b1", memories=["k1"])])
    set_fake(monkeypatch, write_script(tmp_path, DONE_SCRIPT))
    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    assert rc == 0
    prompt = (hub / ".helios" / "runs" / "b1" / "attempt-1" / "prompt.md").read_text()
    assert _memories_section(prompt) == backend.inject(["k1"])
    data = json.loads(
        (hub / ".helios" / "runs" / "b1" / "attempt-1" / "input.json").read_text()
    )
    assert data["input_hashes"]["memory/k1"] == run_mod.sha256_text("hello world")


def test_memories_section_fakebeads_backend_matches_inject(
    tmp_path: Path, monkeypatch
) -> None:
    hub = make_hub(tmp_path)
    cfg = config_mod.load(hub)  # default backend: beads
    beads = beads_mod.FakeBeads([make_bead("b1", memories=["k1"])])
    value = memory_mod.serialize({"source": "hel-1#1", "status": "active"}, "fake body")
    beads.remember("k1", value)
    set_fake(monkeypatch, write_script(tmp_path, DONE_SCRIPT))
    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    assert rc == 0
    prompt = (hub / ".helios" / "runs" / "b1" / "attempt-1" / "prompt.md").read_text()
    backend = memory_mod.BeadsBackend(beads, cfg.hub / cfg.memory.export_dir)
    assert _memories_section(prompt) == backend.inject(["k1"])
    data = json.loads(
        (hub / ".helios" / "runs" / "b1" / "attempt-1" / "input.json").read_text()
    )
    assert data["input_hashes"]["memory/k1"] == run_mod.sha256_text("fake body")


BD = shutil.which("bd")


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_memories_section_real_bd(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    subprocess.run(
        ["bd", "init", "--non-interactive", "--prefix", "t", "--skip-agents", "--quiet"],
        cwd=hub, check=True, capture_output=True,
    )
    real = beads_mod.Beads(hub)
    bead_id = real.create(
        "do it", labels=[],
        metadata={"kind": "impl", "files": ["src/"], "test": "true", "memories": ["k1"]},
    )
    value = memory_mod.serialize({"source": "hel-1#1", "status": "active"}, "real body")
    real.remember("k1", value)
    set_fake(monkeypatch, write_script(tmp_path, DONE_SCRIPT))
    cfg = config_mod.load(hub)
    rc = run_mod.run_one(bead_id, hub=hub, beads=real, config=cfg, harness_override="fake")
    assert rc == 0
    prompt = (hub / ".helios" / "runs" / bead_id / "attempt-1" / "prompt.md").read_text()
    assert "real body" in _memories_section(prompt)


# --------------------------------------------------------------------- item 7


def test_sigint_reentry_guard_blocks_nested_set_and_resets_on_clear(monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(run_mod._INTERRUPT, "set", lambda: calls.append(1))
    run_mod._SIGINT_SETTING = False
    try:
        run_mod._handle_sigint(2, None)
        assert calls == [1]
        assert run_mod._SIGINT_SETTING is True
        # A nested/duplicate SIGINT while the guard is up never re-enters set().
        run_mod._handle_sigint(2, None)
        assert calls == [1]
    finally:
        run_mod._INTERRUPT.clear()
    assert run_mod._SIGINT_SETTING is False


def test_memory_lookup_failure_message_has_no_preflight_prefix(
    tmp_path: Path, capsys
) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1", memories=["m1"])])
    cfg = config_mod.load(hub)

    def boom(key: str) -> bool:
        raise run_mod.MemoryLookupError(f"helios: memory lookup failed for {key}: boom")

    rc = run_mod.run_many(
        ["b1"], hub=hub, beads=beads, config=cfg, harness_override="fake", memory_has=boom
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert err == "helios: memory lookup failed for m1: boom\n"


# --------------------------------------------------------------------- item 9


BLOCKED_SCRIPT = {"exit_code": 0, "sleep_s": 0, "stdout": "x", "session_id": "s1",
                  "report": {"status": "blocked", "summary": "cannot proceed"}}


def test_run_sets_in_progress_before_launch_fakebeads(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    assert beads.show("b1").status == "open"
    set_fake(monkeypatch, write_script(tmp_path, BLOCKED_SCRIPT))
    cfg = config_mod.load(hub)
    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    assert rc == 3
    assert beads.show("b1").status == "in_progress"
    assert not any(b.id == "b1" for b in beads.ready())


def test_run_dry_run_leaves_status_open(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1")])
    set_fake(monkeypatch, write_script(tmp_path, DONE_SCRIPT))
    cfg = config_mod.load(hub)
    rc = run_mod.run_one(
        "b1", hub=hub, beads=beads, config=cfg, harness_override="fake", dry_run=True
    )
    assert rc == 0
    assert beads.show("b1").status == "open"


def test_run_never_reopens_a_closed_bead(tmp_path: Path) -> None:
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1", status="closed")])
    cfg = config_mod.load(hub)
    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    assert rc == 2
    assert beads.show("b1").status == "closed"
    assert beads.argv_log == []


@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_run_sets_in_progress_real_bd(tmp_path: Path, monkeypatch) -> None:
    hub = make_hub(tmp_path)
    subprocess.run(
        ["bd", "init", "--non-interactive", "--prefix", "t", "--skip-agents", "--quiet"],
        cwd=hub, check=True, capture_output=True,
    )
    real = beads_mod.Beads(hub)
    bead_id = real.create("do it", labels=[], metadata={"kind": "impl", "files": ["src/"], "test": "true"})
    assert real.show(bead_id).status == "open"
    set_fake(monkeypatch, write_script(tmp_path, BLOCKED_SCRIPT))
    cfg = config_mod.load(hub)
    rc = run_mod.run_one(bead_id, hub=hub, beads=real, config=cfg, harness_override="fake")
    assert rc == 3
    assert real.show(bead_id).status == "in_progress"
    assert not any(b.id == bead_id for b in real.ready())


# ------------------------------------------------- round-1-fix item 2: no fallbacks


def test_memory_lookup_failure_after_allocation_no_fallback_exit2(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A memory value preflight reported as present but that cannot actually be
    read never becomes "" in the prompt: refuse with exit 2, finalize the
    already-allocated attempt as any launch that never started, and never
    close the bead (round-1-fix item 2). Its not-run checks record detail
    "memory lookup failed", not the generic "interrupted" (round-2-fix
    item 5)."""
    hub = make_hub(tmp_path)
    beads = beads_mod.FakeBeads([make_bead("b1", memories=["m1"])])  # never remembered
    set_fake(monkeypatch, write_script(tmp_path, DONE_SCRIPT))
    cfg = config_mod.load(hub)
    rc = run_mod.run_one("b1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    err = capsys.readouterr().err
    assert rc == 2
    assert "helios: memory lookup failed for m1: " in err
    attempt_dir = hub / ".helios" / "runs" / "b1" / "attempt-1"
    assert not (attempt_dir / "prompt.md").exists()
    state = attempt_mod.read_state(attempt_dir)
    assert state["state"] == "finalized"
    assert state["execution_status"] == "launch_failed"
    env = json.loads((attempt_dir / "envelope.json").read_text())
    assert env["output_commit"] is None
    assert "b1" not in beads.closed
    details = {c["name"]: c["detail"] for c in env["checks"]}
    assert details == {"test": "memory lookup failed", "ownership": "memory lookup failed"}
    assert beads.states.get("b1", {}).get("run") == "failed"


def test_verify_start_no_fallback_to_main_bypassing_preflight(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A verify worktree never falls back to main: calling run_one directly,
    bypassing run_many's preflight, still refuses instead of silently
    starting the worktree from main (round-1-fix item 2, defense in depth)."""
    hub = make_hub(tmp_path)
    parent = beads_mod.Bead(id="impl1", kind="impl", files=["src/"], test="true")
    verify_bead = beads_mod.Bead(
        id="v1", kind="verify-code", unit="U1", parent="impl1",
        files=["tools/verify/U1/"], test="true",
    )
    beads = beads_mod.FakeBeads([parent, verify_bead])
    set_fake(monkeypatch, write_script(tmp_path, DONE_SCRIPT))
    cfg = config_mod.load(hub)
    rc = run_mod.run_one("v1", hub=hub, beads=beads, config=cfg, harness_override="fake")
    err = capsys.readouterr().err
    assert rc == 2
    assert "output_commit" in err
    assert not (hub / ".claude" / "worktrees" / "v1").exists()
    assert not (hub / ".helios" / "runs" / "v1" / "attempt-1").exists()
    assert beads.beads["v1"].status == "open"
