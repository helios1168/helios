"""Prompt and preflight tests (SPEC §7.1 step 2, §7.2)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from helios import prompt as pr
from helios.beads import Bead
from helios.preflight import PreflightContext, check, globs_overlap, memory_map


def make_hub(tmp_path: Path) -> Path:
    hub = tmp_path / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=hub, check=True)
    (hub / ".gitignore").write_text(".helios/\n.claude/worktrees/\n")
    (hub / "skills" / "impl").mkdir(parents=True)
    (hub / "skills" / "impl" / "SKILL.md").write_text(
        "---\nname: impl\n---\n\n# Implementation bead\n\nDo the work.\n"
    )
    (hub / "AGENTS.md").write_text(
        "# helios\n\n## Worker contract\n\nWork exactly one bead.\n\n## Orchestrator\n\nMerges.\n"
    )
    (hub / "docs").mkdir()
    (hub / "docs" / "SPEC.md").write_text(
        "# spec\n\n"
        "## 4. Result contracts\n\nContracts.\n\n"
        "### 4.4 Execution status\n\nStatus words.\n\n"
        "## 5. Configuration\n\nConfig words.\n\n"
        "## 9. Sessions\n\nSessions.\n\n"
        "### 9.3 Events\n\nEvent words.\n"
    )
    subprocess.run(["git", "add", "."], cwd=hub, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=hub, check=True)
    return hub


BEAD = {
    "id": "b1",
    "title": "t",
    "description": "d",
    "kind": "impl",
    "unit": None,
    "accept": "do it",
    "files": ["src/"],
    "test": "uv run pytest -q",
}


def base_kwargs(hub: Path) -> dict:
    return dict(
        kind="impl",
        skills_dir=hub / "skills",
        agents_path=hub / "AGENTS.md",
        bead=dict(BEAD),
        docs=["docs/SPEC.md#5. Configuration"],
        hub=hub,
        memories={"m1": "memory one"},
        worktree=hub / ".claude" / "worktrees" / "b1",
        branch="worktree-b1",
        attempt_id="b1#1",
        report_path=hub / ".helios" / "attempt-1" / "report.json",
        report_schema='{"title": "AgentReport"}',
        inject_cap_bytes=32000,
    )


def test_assemble_order_extraction_and_harness_invariance() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        hub = make_hub(Path(tmp))
        first = pr.assemble(**base_kwargs(hub))
        second = pr.assemble(**base_kwargs(hub))
        assert first == second
        headings = [l for l in first.splitlines() if l.startswith("## ")]
        order = [
            "## Role",
            "## Contract",
            "## Bead",
            "## Docs",
            "## Memories",
            "## Attempt",
        ]
        assert [h for h in headings if h in order] == order
        assert "Do the work." in first and "name: impl" not in first
        assert "Work exactly one bead." in first and "Merges." not in first
        assert "Config words." in first and "Sessions." not in first
        assert '"id": "b1"' in first and "b1#1" in first


def test_assemble_section_keys() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        hub = make_hub(Path(tmp))
        for key in ("5.", "9.3", "4.4", "9.3 Events"):
            kwargs = base_kwargs(hub)
            kwargs["docs"] = [f"docs/SPEC.md#{key}"]
            out = pr.assemble(**kwargs)
            assert "truncated" not in out
        kwargs = base_kwargs(hub)
        kwargs["docs"] = ["docs/SPEC.md#5."]
        assert "Config words." in pr.assemble(**kwargs)
        kwargs["docs"] = ["docs/SPEC.md#9.3"]
        assert "Event words." in pr.assemble(**kwargs)
        kwargs["docs"] = ["docs/SPEC.md#4.4"]
        assert "Status words." in pr.assemble(**kwargs)
        with pytest.raises(KeyError):
            pr.find_section("## a\n\nx\n", "missing")


def test_assemble_fenced_lines_are_never_headings() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        hub = make_hub(Path(tmp))
        (hub / "docs" / "F.md").write_text(
            "## Real\n\nreal words\n\n```\n# fake\n\n## Also fake\n```\n\ntail words\n\n"
            "~~~\n# tilde fake\n~~~\n\nmore words\n"
        )
        text = pr.read_doc(hub, "docs/F.md#Real")[1]
        assert "real words" in text and "tail words" in text and "more words" in text
        with pytest.raises(KeyError):
            pr.find_section("## Real\n\nx\n", "Also fake")


def test_heading_and_fence_shapes() -> None:
    assert pr._headings("#nospace\n") == []
    assert pr._headings("####### seven\n") == []
    assert pr._headings("## Title\n") == [(0, 2, "Title", "Title")]
    assert pr._headings("#\n") == [(0, 1, "", "")]
    long_fence = "````\n``` still open\n````\n\n## After\n"
    assert pr.find_section(long_fence, "After").startswith("## After")
    unclosed = "## A\n\ntext\n\n```\n# never\n"
    assert pr.find_section(unclosed, "A").count("# never") == 1
    with pytest.raises(KeyError):
        pr.find_section(unclosed, "never")


def test_heading_whitespace_shapes() -> None:
    assert pr.find_section("   ## B\n\nbody\n", "B").startswith("   ## B")
    assert pr.find_section("#\tA\n\nbody\n", "A").startswith("#\tA")
    assert pr.find_section("## B\n\nbody\n", "B").startswith("## B")
    with pytest.raises(KeyError):
        pr.find_section("    ## B\n\nbody\n", "B")


def test_docs_and_memories_strip_only_leading_and_trailing_newlines() -> None:
    """Only leading/trailing ``\\n`` characters are removed (SPEC §7.2 item 5,
    decided in hel-67j): a first line's indentation and other whitespace
    (including a lone leading/trailing space) survive."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        hub = make_hub(Path(tmp))
        (hub / "docs" / "IND.md").write_text("    indented doc line\nmore text\n")
        kwargs = base_kwargs(hub)
        kwargs["docs"] = ["docs/IND.md"]
        kwargs["memories"] = {
            "m1": "    indented code\nline2\n\n",
            "m2": "\n\ntext\n\n",
            "m3": "  x  ",
        }
        out = pr.assemble(**kwargs)
        assert "### docs/IND.md\n\n    indented doc line\nmore text" in out
        assert "### m1\n\n    indented code\nline2" in out
        assert "### m2\n\ntext" in out
        assert "### m3\n\n  x  " in out


def test_assemble_caps_docs_plus_memories() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        hub = make_hub(Path(tmp))
        (hub / "docs" / "BIG.md").write_text("# big\n\n" + "x" * 5000 + "\n")
        kwargs = base_kwargs(hub)
        kwargs["docs"] = ["docs/BIG.md"]
        kwargs["memories"] = {"m1": "y" * 5000}
        kwargs["inject_cap_bytes"] = 1000
        out = pr.assemble(**kwargs)
        assert "truncated for inject cap" in out
        assert "docs/BIG.md" in out or "m1" in out
        docs_text = out.split("## Docs\n\n")[1].split("\n\n## Memories")[0]
        memories_text = out.split("## Memories\n\n")[1].split("\n\n## Attempt")[0]
        assert len((docs_text + memories_text).encode("utf-8")) <= 1000


def test_apply_cap_keeps_longest_fitting_prefix() -> None:
    part = lambda n, s: (n, f"### {n}\n\n" + "a" * (s - len(f"### {n}\n\n")))
    docs = [
        part("docs/gen/d1243_0.md", 150),
        part("docs/gen/d1243_1.md", 101),
        part("docs/gen/d1243_2.md", 715),
    ]
    mems = [part("m" * 16 + "0", 620), part("m" * 18 + "1", 620)]
    kept_docs, kept_mems, note = pr._apply_cap(docs, mems, 1312)
    assert pr._capped_bytes(kept_docs, kept_mems, note) <= 1312
    assert note.startswith("[truncated for inject cap: ")


def test_apply_cap_randomized_prefix_and_cap() -> None:
    import random

    rng = random.Random(1243)
    for trial in range(50):
        names = [f"item-{i}" for i in range(rng.randint(1, 6))]
        items = [(n, f"### {n}\n\n" + "x" * rng.randint(0, 400)) for n in names]
        docs, mems = items[: len(items) // 2], items[len(items) // 2 :]
        cap = rng.randint(0, 1200)
        kept_docs, kept_mems, note = pr._apply_cap(docs, mems, cap)
        assert pr._capped_bytes(kept_docs, kept_mems, note) <= cap
        kept_names = [n for n, _ in docs[: len(kept_docs)]] + [
            n for n, _ in mems[: len(kept_mems)]
        ]
        assert kept_names == names[: len(kept_names)]
        cut = names[len(kept_names) :]
        if cut:
            assert note
            if len(pr._note_text(cut).encode("utf-8")) <= cap:
                assert all(n in note for n in cut)
            assert len(note.encode("utf-8")) <= cap
        else:
            assert note == ""


def test_assemble_cap_never_splits_a_character() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        hub = make_hub(Path(tmp))
        (hub / "docs" / "UNI.md").write_text("# u\n\n" + "é" * 500 + "\n")
        kwargs = base_kwargs(hub)
        kwargs["docs"] = ["docs/UNI.md"]
        kwargs["memories"] = {"m1": "ü" * 500}
        kwargs["inject_cap_bytes"] = 100
        out = pr.assemble(**kwargs)
        out.encode("utf-8")
        assert "truncated for inject cap" in out


def impl_bead(
    *,
    id: str = "b1",
    files: list[str] | None = None,
    test: str = "uv run pytest -q",
    docs: list[str] | None = None,
    memories: list[str] | None = None,
) -> Bead:
    return Bead(
        id=id,
        kind="impl",
        files=["src/"] if files is None else files,
        test=test,
        docs=[] if docs is None else docs,
        memories=[] if memories is None else memories,
    )


def test_preflight_needs_files_test_docs_and_memories(tmp_path: Path) -> None:
    hub = make_hub(tmp_path)
    ctx = PreflightContext(hub=hub, memory_has=memory_map({}))
    errors = check(
        [impl_bead(files=[], test="", docs=["docs/MISSING.md"], memories=["nope"])], ctx
    )
    assert any("needs `files`" in e for e in errors)
    assert any("needs `test`" in e for e in errors)
    assert any("MISSING" in e for e in errors)
    assert any("nope" in e for e in errors)
    assert check([impl_bead()], PreflightContext(hub=hub, memory_has=memory_map({}))) == []


def test_preflight_docs_key_must_resolve(tmp_path: Path) -> None:
    hub = make_hub(tmp_path)
    ctx = PreflightContext(hub=hub, memory_has=memory_map({}))
    ok = check([impl_bead(docs=["docs/SPEC.md#5."])], ctx)
    assert ok == []
    bad = check([impl_bead(docs=["docs/SPEC.md#nope"])], ctx)
    assert any("nope" in e for e in bad)


def test_preflight_memory_lookup_is_required() -> None:
    with pytest.raises(TypeError):
        PreflightContext(hub=Path("/tmp"))  # type: ignore[call-arg]


def test_preflight_verify_math_model_and_overlap(tmp_path: Path) -> None:
    hub = make_hub(tmp_path)
    (hub / "docs" / "units").mkdir(parents=True)
    (hub / "docs" / "units" / "U1.md").write_text("# U1\n\n## Model\n\n_empty_\n")
    ctx = PreflightContext(hub=hub, memory_has=memory_map({}), units_dir="docs/units")
    bead = Bead(id="v1", kind="verify-math", unit="U1", parent="b1")
    assert any("substantive" in e for e in check([bead], ctx))
    (hub / "docs" / "units" / "U1.md").write_text("# U1\n\n## Model\n\n" + "claim words " * 30 + "\n")
    assert check([bead], ctx) == []
    clash = check([impl_bead(id="a", files=["src/helios/"]), impl_bead(id="b", files=["src/"])], ctx)
    assert any("overlap" in e for e in clash)
    disjoint = check(
        [impl_bead(id="a", files=["src/helios/"]), impl_bead(id="b", files=["tests/"])], ctx
    )
    assert disjoint == []


def test_preflight_model_counts_text_not_newlines(tmp_path: Path) -> None:
    hub = make_hub(tmp_path)
    (hub / "docs" / "units").mkdir(parents=True)
    (hub / "docs" / "units" / "U1.md").write_text("# U1\n\n## Model\n\n" + "x\n" * 101)
    ctx = PreflightContext(hub=hub, memory_has=memory_map({}), units_dir="docs/units")
    bead = Bead(id="v1", kind="verify-math", unit="U1", parent="b1")
    assert any("substantive" in e for e in check([bead], ctx))


def test_preflight_model_section_is_exact_level_two(tmp_path: Path) -> None:
    hub = make_hub(tmp_path)
    (hub / "docs" / "units").mkdir(parents=True)
    ctx = PreflightContext(hub=hub, memory_has=memory_map({}), units_dir="docs/units")
    bead = Bead(id="v1", kind="verify-math", unit="U1", parent="b1")
    unit = hub / "docs" / "units" / "U1.md"
    unit.write_text(
        "# U: t\n\nStatus: open\n\n## Brief\n\n### Model scope\n\n"
        + "s" * 300
        + "\n\n## Model\n\n_empty_\n\n## Verify\n\n_empty_\n"
    )
    assert any("substantive" in e for e in check([bead], ctx))
    unit.write_text(
        "# U: t\n\n## Model\n\n"
        + "a" * 150
        + "\n\n### Model detail\n\n"
        + "b" * 100
        + "\n\n## Verify\n\n_empty_\n"
    )
    assert check([bead], ctx) == []
    unit.write_text("# U\n\n   ## Model\n\n" + "z" * 250 + "\n")
    assert check([bead], ctx) == []
    unit.write_text("# U\n\n    ## Model\n\n" + "z" * 250 + "\n")
    assert any("no ## Model" in e for e in check([bead], ctx))
    unit.write_text("# U1\n\n## Model\n\n```python\n# " + "k" * 250 + "\n```\n")
    assert check([bead], ctx) == []
    unit.write_text("# U1\n\n## Model\n\n#" + "k" * 250 + "\n")
    assert check([bead], ctx) == []


def test_preflight_docs_directory_is_an_error(tmp_path: Path) -> None:
    hub = make_hub(tmp_path)
    (hub / "docs" / "sub").mkdir()
    ctx = PreflightContext(hub=hub, memory_has=memory_map({}))
    errors = check([impl_bead(docs=["docs/sub"])], ctx)
    assert any("directory" in e for e in errors)


def test_globs_overlap_rule() -> None:
    assert globs_overlap("tests/test_*.py", "tests/*_config.py")
    assert globs_overlap("src/", "*.py")
    assert not globs_overlap("src/helios/config.py", "src/helios/beads.py")
    assert not globs_overlap("src/a/", "src/b/*.py")


def test_preflight_gitignore_must_ignore_helios_and_worktrees(tmp_path: Path) -> None:
    """Default config (SPEC §5): project.runs is `.helios/runs`, project.worktrees is
    `.claude/worktrees`. A hub that ignores the parent `.helios/` is unaffected, since
    git recognizes the nested probe as ignored too (SPEC §7.1 step 2)."""
    hub = tmp_path / "hub2"
    hub.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=hub, check=True)
    ctx = PreflightContext(hub=hub, memory_has=memory_map({}))
    bead = impl_bead()

    (hub / ".gitignore").write_text("")
    assert check([bead], ctx) == [
        ".gitignore must ignore .helios/runs/",
        ".gitignore must ignore .claude/worktrees/",
    ]

    (hub / ".gitignore").write_text(".claude/worktrees/\n")
    assert check([bead], ctx) == [".gitignore must ignore .helios/runs/"]

    (hub / ".gitignore").write_text(".helios/\n")
    assert check([bead], ctx) == [".gitignore must ignore .claude/worktrees/"]

    (hub / ".gitignore").write_text(".helios/\n.claude/worktrees/\n")
    assert check([bead], ctx) == []


def test_preflight_gitignore_probes_configured_directories(tmp_path: Path) -> None:
    """A hub with non-default `project.worktrees` and `project.runs` is checked against
    those configured directories, not the hardcoded defaults (hel-4xv)."""
    hub = tmp_path / "hub2b"
    hub.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=hub, check=True)
    ctx = PreflightContext(
        hub=hub,
        memory_has=memory_map({}),
        runs_rel=".helios/runs",
        worktrees_rel=".helios-worktrees",
    )
    bead = impl_bead()

    (hub / ".gitignore").write_text(".helios/\n.helios-worktrees/\n")
    assert check([bead], ctx) == []

    (hub / ".gitignore").write_text(".helios/\n")
    errors = check([bead], ctx)
    assert errors == [".gitignore must ignore .helios-worktrees/"]
    assert not any(".claude/worktrees/" in e for e in errors)


def test_preflight_gitignore_runs_once_per_call_not_per_bead(tmp_path: Path) -> None:
    """SPEC §7.1 step 2: the gitignore check runs once per preflight call, not once
    per bead, so a batch of beads gets one copy of the refusal, not one per bead."""
    hub = tmp_path / "hub2c"
    hub.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=hub, check=True)
    (hub / ".gitignore").write_text("")
    ctx = PreflightContext(hub=hub, memory_has=memory_map({}))
    errors = check(
        [impl_bead(id="b1", files=["src/a/"]), impl_bead(id="b2", files=["src/b/"])], ctx
    )
    assert errors.count(".gitignore must ignore .helios/runs/") == 1
    assert errors.count(".gitignore must ignore .claude/worktrees/") == 1


def test_preflight_gitignore_honors_git_info_exclude(tmp_path: Path) -> None:
    hub = tmp_path / "hub3"
    hub.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=hub, check=True)
    (hub / ".gitignore").write_text(".claude/worktrees/\n")
    (hub / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (hub / ".git" / "info" / "exclude").write_text(".helios/\n")
    ctx = PreflightContext(hub=hub, memory_has=memory_map({}))
    assert check([impl_bead()], ctx) == []


def test_preflight_unfinalized_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import sys

    from helios import attempt as att

    hub = make_hub(tmp_path)
    ctx = PreflightContext(hub=hub, memory_has=memory_map({}))
    bead = impl_bead()
    assert check([bead], ctx) == []
    # A dead attempt is recoverable, so preflight passes.
    attempt, _ = att.allocate(hub=hub, runs_rel=".helios/runs", bead="b1", worktree=tmp_path)
    assert check([bead], ctx) == []
    att.transition(attempt.dir, "finalized")
    assert check([bead], ctx) == []
    # A live pid (a real process-group leader whose start time matches)
    # refuses, naming attach and stop (SPEC §9.2 liveness rule, item 4).
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    try:
        pid_start = att.read_pid_start(proc.pid)
        live, _ = att.allocate(
            hub=hub, runs_rel=".helios/runs", bead="b1", worktree=tmp_path, pid=proc.pid
        )
        att.transition(live.dir, "launched", pid_start=pid_start)
        errors = check([bead], ctx)
        assert any("helios attach b1" in e and "helios stop b1" in e for e in errors)
    finally:
        proc.kill()
        proc.wait()
    # A reused pid (its current start time no longer matches the recorded
    # one) is not live, so preflight does not refuse it (rule item 4).
    reused, _ = att.allocate(
        hub=hub, runs_rel=".helios/runs", bead="b1", worktree=tmp_path, pid=os.getpid()
    )
    att.transition(reused.dir, "launched", pid_start="original-start-time")
    monkeypatch.setattr(att, "read_pid_start", lambda pid: "a-different-start-time")
    assert check([bead], ctx) == []


def test_preflight_int_pid_start_with_live_group_refuses(tmp_path: Path) -> None:
    """A pid_start that is not a string (SPEC §8.3) reads as null, so liveness
    falls back to the process-group test alone: same outcome as a live group
    with a genuinely null pid_start (hel-lpw)."""
    import sys

    from helios import attempt as att

    hub = make_hub(tmp_path)
    ctx = PreflightContext(hub=hub, memory_has=memory_map({}))
    bead = impl_bead()
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    try:
        live, _ = att.allocate(
            hub=hub, runs_rel=".helios/runs", bead="b1", worktree=tmp_path, pid=proc.pid
        )
        att.transition(live.dir, "launched", pid_start=123)  # type: ignore[arg-type]
        errors = check([bead], ctx)
        assert any("helios attach b1" in e and "helios stop b1" in e for e in errors)
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.parametrize("bad_pid", [2**31, "123", 0, True, -1, 1.5])
def test_preflight_bad_pid_values_never_raise_and_are_not_live(
    tmp_path: Path, bad_pid: object
) -> None:
    """Each of these bad-shaped pids (hel-lpw) must never raise inside
    preflight and must read as not live, so preflight does not refuse."""
    from helios import attempt as att

    hub = make_hub(tmp_path)
    ctx = PreflightContext(hub=hub, memory_has=memory_map({}))
    bead = impl_bead()
    launched, _ = att.allocate(hub=hub, runs_rel=".helios/runs", bead="b1", worktree=tmp_path)
    att.transition(launched.dir, "launched", pid=bad_pid)  # type: ignore[arg-type]
    assert check([bead], ctx) == []
