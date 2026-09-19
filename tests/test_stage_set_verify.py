"""Adversarial verification of the stage set refactor (bead hel-lnk).

This file writes no production code. Each test either demonstrates a defect (it fails on
the current tree) or records a verdict reached by trying to break something and failing
(it passes). The docstring of every test says which it is.

Three targets, then whatever else the refactor's shape suggested:

1. ``run._classify_extra_paths`` against ``ownership.check``.
2. An empty ``Bead.kind`` through every consumer of a kind.
3. The layer boundary test in tests/test_stages.py.
"""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

from helios import beads as beads_mod
from helios import config as config_mod
from helios import control as control_mod
from helios import learned as learned_mod
from helios import ownership as ownership_mod
from helios import preflight as preflight_mod
from helios import run as run_mod
from helios import stageset

# The layer boundary test this file attacks, read as data: its own predicate, module list
# and reviewed exemptions.
import test_stages as boundary

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Target 1: the two copies of the SPEC 7.4 per-path rule.
# ---------------------------------------------------------------------------

# One probe per branch of the rule, plus the cases the bead named: a path equal to a
# scope or always_allowed prefix with no trailing slash, the memory export directory, a
# confidential glob without a slash, and the always rejected prefixes.
PROBE_PATHS = [
    "src/a.py",
    "src/sub/a.py",
    "tests/a.py",
    "tests",
    "docs/x.md",
    ".beads/issues.jsonl",
    ".beads",
    ".helios/runs/x",
    ".agents/workflow.toml",
    ".helios/memories/m.md",
    ".helios/memories",
    "tools/verify/u1/art.txt",
    "tools/verify/u1",
    "tools/verify",
    "keys/a.pem",
    "a.pem",
]

PROBE_CASES = [
    ("files", "u1", ()),
    ("files", None, ("*.pem",)),
    ("artifacts", "u1", ()),
    ("artifacts", None, ()),
    ("artifacts", "", ()),
    ("none", "u1", ()),
    ("none", None, ("*.pem",)),
    ("none", "u1", ("tools/verify/u1/*",)),
]


@pytest.mark.parametrize("mode,unit,confidential", PROBE_CASES)
def test_classify_extra_paths_agrees_with_ownership_check(
    monkeypatch: pytest.MonkeyPatch, mode: str, unit: str | None, confidential: tuple[str, ...]
) -> None:
    """Verdict, no defect found: the copy in run.py cannot disagree per path.

    ``ownership.changed_paths`` is stubbed so ``ownership.check`` classifies exactly the
    list ``_classify_extra_paths`` is handed, which is the only way to compare the two
    rules on the same input: ``check`` otherwise takes its paths from git and the copy
    takes them from a caller. Every branch of the rule is probed under every ownership
    mode, with ``unit`` None and empty under ``artifacts``, a path equal to a prefix
    with no trailing slash, the memory export directory and a confidential glob with no
    slash.
    """
    monkeypatch.setattr(ownership_mod, "changed_paths", lambda *a, **k: list(PROBE_PATHS))
    kwargs = dict(
        files=["src/*.py", "docs/"],
        always_allowed=("tests/",),
        mode=mode,
        verify_artifacts="tools/verify",
        unit=unit,
        confidential=confidential,
        memory_export_dir=".helios/memories",
    )
    result = ownership_mod.check(worktree=Path("/nonexistent"), base_commit="HEAD", **kwargs)
    allowed, rejected = run_mod._classify_extra_paths(list(PROBE_PATHS), **kwargs)
    assert list(result.allowed) == allowed
    assert list(result.rejected) == rejected


def test_classify_extra_paths_and_ownership_check_share_their_helpers() -> None:
    """Verdict, no defect found: the copy reuses the originals rather than re-spelling them.

    The only helper run.py spells again is ``_is_prefix_match``, and the two definitions
    are the same source text. This test is what would fail if one of them were edited
    alone, which is the drift the bead was worried about.
    """
    assert run_mod.ownership_mod is ownership_mod
    import inspect

    assert inspect.getsource(run_mod._is_prefix_match).split("\n", 1)[1] == (
        inspect.getsource(ownership_mod._is_prefix_match).split("\n", 1)[1]
    )


def test_link_into_worktrees_is_not_needed_by_the_copy(tmp_path: Path) -> None:
    """Verdict, no defect found: the missing ``link_into_worktrees`` argument cannot matter.

    ``ownership.check`` passes it to ``changed_paths``, which drops an *untracked*
    symlink matching one of the globs. Every path reaching ``_classify_extra_paths``
    comes from ``git diff --cached``, so it has an index entry and is by definition not
    untracked. The glob therefore has nothing to exclude there.
    """
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    (repo / ".env").symlink_to("/etc/hostname")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert ".env" not in ownership_mod.changed_paths(
        repo, base, link_into_worktrees=(".env",)
    )
    subprocess.run(["git", "add", ".env"], cwd=repo, check=True)
    assert ".env" in ownership_mod.changed_paths(repo, base, link_into_worktrees=(".env",))


# ---------------------------------------------------------------------------
# Target 1, the defect: the caller, not the rule.
# ---------------------------------------------------------------------------


def _hub(tmp_path: Path) -> Path:
    hub = tmp_path / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=hub, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=hub, check=True)
    (hub / "README.md").write_text("hi\n")
    (hub / ".gitignore").write_text(".helios/\n.claude/worktrees/\n")
    (hub / "skills" / "impl").mkdir(parents=True)
    (hub / "skills" / "impl" / "SKILL.md").write_text("---\nname: impl\n---\n\nDo the work.\n")
    (hub / "AGENTS.md").write_text(
        "# helios\n\n## Worker contract\n\nWork one bead.\n\n## Orchestrator\n\nMerges.\n"
    )
    subprocess.run(["git", "add", "."], cwd=hub, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=hub, check=True)
    return hub


def _fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: dict) -> None:
    path = tmp_path / "script.json"
    path.write_text(json.dumps(payload))
    monkeypatch.setenv("HELIOS_FAKE_SCRIPT", str(path))


def _envelope(hub: Path, bead: str, n: int) -> dict:
    return json.loads(
        (hub / ".helios" / "runs" / bead / f"attempt-{n}" / "envelope.json").read_text()
    )


def test_staged_only_path_is_classified_once(tmp_path: Path, monkeypatch) -> None:
    """DEFECT, proved. run.py:1174 can hand ``_classify_extra_paths`` the same path twice.

    ``extra`` is built from ``[*cached_base, *cached_head]`` with no de-duplication. In
    a fresh worktree HEAD equals ``base_commit``, so the two git calls return the same
    list and every staged-only path is classified, and reported, twice.
    ``ownership.check`` cannot do this: ``changed_paths`` returns a sorted set. Input:
    an attempt whose test stages a file and then removes it from the worktree, so the
    path is in the index but in neither diff against the worktree.
    """
    hub = _hub(tmp_path)
    (hub / ".agents").mkdir(exist_ok=True)
    (hub / ".agents" / "workflow.toml").write_text('[project]\nconfidential = ["*.pem"]\n')
    subprocess.run(["git", "add", "."], cwd=hub, check=True)
    subprocess.run(["git", "commit", "-qm", "conf"], cwd=hub, check=True)
    _fake(monkeypatch, tmp_path, {"report": {"status": "done", "summary": "ok"}})
    beads = beads_mod.FakeBeads(
        [
            beads_mod.Bead(
                id="b1",
                kind="impl",
                files=["src/b1/"],
                test="echo KEY > k.pem && git add k.pem && rm k.pem",
            )
        ]
    )
    run_mod.run_one(
        "b1", hub=hub, beads=beads, config=config_mod.load(hub), harness_override="fake"
    )
    detail = next(
        c["detail"] for c in _envelope(hub, "b1", 1)["checks"] if c["name"] == "ownership"
    )
    assert detail.count("k.pem") == 1, detail


# ---------------------------------------------------------------------------
# Target 2: an empty Bead.kind, as a real bd bead missing its kind metadata produces it.
# ---------------------------------------------------------------------------


def _bd_payload(**over: object) -> dict:
    """One `bd show --json` object for a bead with no kind metadata and no kind label."""
    payload: dict = {
        "id": "hel-x1",
        "title": "no kind anywhere",
        "status": "open",
        "labels": ["unit:u1"],
        "metadata": {"unit": "u1", "files": ["src/"], "test": "true"},
    }
    payload.update(over)
    return payload


def test_from_show_on_real_bd_data_leaves_the_kind_empty() -> None:
    """Verdict: the input every later test uses is what bd really produces."""
    bead = beads_mod.Bead.from_show(_bd_payload())
    assert bead.kind == ""
    assert beads_mod.Bead.from_show(_bd_payload(labels=["kind:"])).kind == ""


def test_preflight_refuses_an_empty_kind_naming_the_bead(tmp_path: Path) -> None:
    """Verdict, no defect found: the refusal names the bead and the declared ids."""
    ctx = preflight_mod.PreflightContext(hub=tmp_path, memory_has=lambda key: True)
    errors = preflight_mod.check([beads_mod.Bead.from_show(_bd_payload())], ctx)
    assert any(e.startswith("hel-x1: '' is not a declared stage; declared: ") for e in errors), errors


def test_spec_for_kind_refuses_an_empty_kind() -> None:
    """Verdict, no defect found: the resolver refuses rather than guessing a stage."""
    with pytest.raises(ValueError, match="unknown bead kind ''"):
        config_mod.spec_for_kind(config_mod.Config(hub=Path("/x")), "")


def test_candidate_selection_and_learned_skip_an_empty_kind() -> None:
    """Verdict, no defect found, but silent: an empty kind is never a candidate.

    ``control.candidates`` and ``learned._helios_beads`` both test ``kind in stages``
    and the ``kind:<id>`` label, so a bead whose kind metadata is gone drops out of
    ``helios next``, ``helios unit run`` and the learned queue with no message. That is
    not a regression: under the old ``"impl"`` default such a bead still lacked the
    ``kind:impl`` label, so it was skipped then too.
    """
    stages = stageset.research()
    beads = beads_mod.FakeBeads([beads_mod.Bead.from_show(_bd_payload())])
    assert control_mod.candidates(beads, "u1", stages=stages) == []
    assert learned_mod._helios_beads(beads, stages) == []


def test_run_one_refuses_an_empty_kind_before_anything_is_created(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Verdict, no defect found, in outcome or wording: closed by the finding 2 fix.

    ``run_one`` exits 2 and creates no attempt. Before the fix the printed reason was
    ``skills//SKILL.md does not exist in the hub``, the wording defect described below,
    because ``_missing_skill`` builds ``hub / "skills" / kind / "SKILL.md"`` and an empty
    kind collapses that to ``hub/skills/SKILL.md``. Now the stage check runs first, so
    the reason is always that the kind is undeclared, the same wording preflight uses.
    """
    hub = _hub(tmp_path)
    _fake(monkeypatch, tmp_path, {"report": {"status": "done", "summary": "ok"}})
    beads = beads_mod.FakeBeads([beads_mod.Bead.from_show(_bd_payload())])
    code = run_mod.run_one(
        "hel-x1", hub=hub, beads=beads, config=config_mod.load(hub), harness_override="fake"
    )
    assert code == 2
    assert not (hub / ".helios" / "runs" / "hel-x1").exists()
    assert "preflight: hel-x1: '' is not a declared stage; declared: " in capsys.readouterr().err


def test_run_one_refuses_an_undeclared_kind_behind_a_hub_skill_file(
    tmp_path: Path, monkeypatch
) -> None:
    """DEFECT, proved, reachable only through the library API.

    ``run_one`` repeats two preflight checks of its own, the missing skill and the
    closed bead, but not the one that matters here: that the kind is a declared stage.
    An empty kind makes ``_missing_skill`` probe ``hub/skills/SKILL.md``, so a hub that
    happens to have that file passes the only guard, and ``config.spec_for_kind`` then
    raises a plain ``ValueError`` out of ``run_one``. ``cli.main`` catches
    ``ConfigError`` only, so a caller that reaches this sees a traceback instead of
    exit 2. Every command path preflights first, so this is defense in depth that is
    missing, not a live crash.
    """
    hub = _hub(tmp_path)
    (hub / "skills" / "SKILL.md").write_text("# how skills work\n")
    _fake(monkeypatch, tmp_path, {"report": {"status": "done", "summary": "ok"}})
    beads = beads_mod.FakeBeads([beads_mod.Bead.from_show(_bd_payload())])
    code = run_mod.run_one(
        "hel-x1", hub=hub, beads=beads, config=config_mod.load(hub), harness_override="fake"
    )
    assert code == 2


def test_writeback_close_rule_treats_an_unknown_gate_as_report() -> None:
    """Verdict, no defect found: the fallthrough matches ``StageSpec``'s own default.

    ``beads.should_close`` ends in ``return True`` for any gate that is neither ``none``
    nor ``verdict``, so a gate outside ``stageset.GATES`` is judged like ``report``.
    That is the default ``StageSpec.gate``, and ``stageset.parse`` refuses any other
    value, so no workflow.toml can reach the fallthrough at all. Recorded because the
    function is public and takes a bare string, not because anything is wrong.
    """
    assert beads_mod.should_close(
        gate="",
        execution_status="completed",
        report_status="done",
        checks_passed=True,
        verdict=None,
    )
    assert not beads_mod.should_close(
        gate="",
        execution_status="completed",
        report_status="blocked",
        checks_passed=True,
        verdict=None,
    )


# ---------------------------------------------------------------------------
# Target 3: the layer boundary rule.
# ---------------------------------------------------------------------------

HIDDEN_FORMS = {
    'STOP = "frame model report".split()': {"frame", "model", "report"},
    'if bead.kind == "verify" "-code": pass': {"verify-code"},
    'KINDS = {"impl", "validate"} | {"frame survey"}': {"impl", "validate", "frame", "survey"},
    'default_kind = "imp" "l"': {"impl"},
}

# DEFECT, closed: the old rule was ``_quoted_stage_ids_in``, a per-line regex matching
# ``(["'])<id>\1``, so a stage id lost its guard as soon as it shared a literal with another
# word (``"frame model report".split()``) or was written as two adjacent literals, which
# Python concatenates at compile time. Both are ordinary Python, and both were how a
# hardcoded stage id got back into a core module with the guard still green. The fix deletes
# that rule from tests/test_stages.py rather than patch it, so there is nothing left to call
# here; ``test_stronger_rule_catches_every_hidden_form`` below proves its replacement sees
# every one of these forms, on the same four inputs this dict recorded the finding with.


def test_core_module_list_covers_every_core_module() -> None:
    """DEFECT, proved: ``CORE_MODULES`` is a hand list, so a core module can be missing.

    ``src/helios/__init__.py`` is missing from it today. It carries no stage id, so
    nothing is wrong in the tree, but the guard's coverage is whatever someone
    remembered to type, and the comment above the list says so out loud. Deriving the
    list from the filesystem minus the named pack modules closes that, which is what
    this test does.
    """
    assert sorted(_core_modules_from_disk()) == sorted(boundary.CORE_MODULES)


# The research pack of SPEC section 3, "Core and pack": the section 3.2 declaration, the
# program and claims modules of section 15 and their commands. stageset.py is the schema
# stage ids are checked against, not the core, and tests/test_stages.py records why.
PACK = (
    "src/helios/program.py",
    "src/helios/stageset.py",
    "src/helios/claims/__init__.py",
    "src/helios/claims/runner.py",
    "src/helios/commands/claims_attack.py",
    "src/helios/commands/claims_check.py",
    "src/helios/commands/program_check.py",
    "src/helios/commands/program_diff.py",
    "src/helios/commands/program_show.py",
)


def _core_modules_from_disk() -> list[str]:
    return [
        str(path.relative_to(REPO))
        for path in sorted((REPO / "src" / "helios").rglob("*.py"))
        if str(path.relative_to(REPO)) not in PACK
    ]


def _string_constants(text: str) -> list[tuple[int, str]]:
    """Every string literal in the source except a docstring, with its line number.

    ``ast`` joins adjacent literals into one constant, so this sees a concatenated id
    the line based rule misses. A docstring is a string that is the whole of an
    expression statement; prose is not a hardcoded stage id and would drown the signal.
    """
    tree = ast.parse(text)
    docstrings = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _hardcoded_ids(value: str) -> set[str]:
    """The stage ids a string literal hardcodes, under a rule prose cannot trip.

    Two forms count. The literal is exactly a stage id, which is the existing rule but
    read off the compiled constant, so two adjacent literals are one id here. Or every
    whitespace separated word of it is a stage id and there are at least two, which is
    the ``"frame model report".split()`` form. An English sentence that happens to
    contain "report" or "model" fails both, which is why neither needs an exclusion.
    """
    words = value.split()
    if len(words) == 1 and words[0] == value and value in boundary.RESEARCH_STAGE_IDS:
        return {value}
    if len(words) > 1 and all(word in boundary.RESEARCH_STAGE_IDS for word in words):
        return set(words)
    return set()


def test_no_core_module_hides_a_stage_id_in_a_longer_literal() -> None:
    """The stronger rule the two defects above ask for, run over the core.

    Same duty as ``test_stages.test_core_modules_name_no_research_stage_id``, closing the
    two holes proved above: the module list comes from the filesystem, and an id is read
    off the compiled string constant rather than the source line, so implicit
    concatenation and a multi-id literal are both caught. The exemptions are
    tests/test_stages.py's own reviewed list, keyed the same way, so the two cannot drift.
    """
    violations = []
    for rel in _core_modules_from_disk():
        text = (REPO / rel).read_text()
        lines = text.splitlines()
        for lineno, value in _string_constants(text):
            hits = _hardcoded_ids(value)
            if not hits:
                continue
            if lines[lineno - 1].strip() in boundary.ALLOWED_LINES:
                continue
            violations.append(f"{rel}:{lineno}: names {sorted(hits)!r}: {lines[lineno - 1].strip()}")
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("line,want", sorted(HIDDEN_FORMS.items()))
def test_stronger_rule_catches_every_hidden_form(line: str, want: set[str]) -> None:
    """The stronger rule sees what the line based one misses, on the same four inputs."""
    hits: set[str] = set()
    for _lineno, value in _string_constants(line):
        hits |= _hardcoded_ids(value)
    assert hits == want, line


def test_no_core_module_tests_a_kind_against_a_stage_id_prefix() -> None:
    """The third hole, which no literal rule can see, and the test that closes it.

    ``kind.startswith("verify")`` is the branch the refactor deleted, and neither the
    line based rule nor the stronger one above can catch it: ``"verify"`` is not a stage
    id, so there is no id to find. It is still a research stage assumption in the core,
    because only the section 3.2 pack makes "verify" a meaningful prefix. This rule
    catches it by shape instead: a prefix or suffix test, on something spelled kind or
    stage, against a literal that is a piece of a research stage id. Passes today, which
    is the point.
    """
    violations = []
    pieces = {
        (stage_id[:n], stage_id)
        for stage_id in boundary.RESEARCH_STAGE_IDS
        for n in range(1, len(stage_id) + 1)
    } | {
        (stage_id[-n:], stage_id)
        for stage_id in boundary.RESEARCH_STAGE_IDS
        for n in range(1, len(stage_id) + 1)
    }
    for rel in _core_modules_from_disk():
        for node in ast.walk(ast.parse((REPO / rel).read_text())):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in ("startswith", "endswith") or not node.args:
                continue
            receiver = ast.unparse(node.func.value)
            if "kind" not in receiver and "stage" not in receiver:
                continue
            arg = node.args[0]
            if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
                continue
            named = sorted({full for piece, full in pieces if piece == arg.value})
            if named:
                violations.append(
                    f"{rel}:{node.lineno}: {receiver}.{node.func.attr}({arg.value!r}) "
                    f"is a piece of {named!r}"
                )
    assert not violations, "\n".join(violations)


# ---------------------------------------------------------------------------
# Outside the three targets.
# ---------------------------------------------------------------------------


def test_verifies_may_point_at_a_stage_declared_later() -> None:
    """Verdict: accepted by the parser, and ``helios unit new`` can never use it.

    ``parse`` collects every id before it checks ``verifies``, so a forward reference is
    legal. ``units._validate`` then refuses the stage whatever ``--stages`` says,
    because the parent must appear *earlier* in the requested list, and the requested
    list must be in declaration order. The set is accepted and one of its stages is
    dead. SPEC section 3.1's refusal list does not cover this, so it is a spec gap
    rather than a code defect, and the assertion below records today's behavior.
    """
    stages = stageset.parse(
        [
            {"id": "checker", "author": "orchestrate", "verifies": "worker"},
            {"id": "worker", "author": "orchestrate"},
        ]
    )
    assert stages.parent_of("checker") == "worker"
    assert stages.index("checker") < stages.index("worker")


def test_two_stages_may_verify_the_same_stage() -> None:
    """Verdict, no defect found: nothing downstream assumes one verifier per stage.

    ``verify_ids`` is a set of the verifying stages, ``parent_of`` is a lookup on the
    verifier, and ``units._parent_index`` resolves each verifier independently, so both
    parent onto the same bead and the dependency chain stays linear.
    """
    stages = stageset.parse(
        [
            {"id": "worker", "author": "orchestrate"},
            {"id": "check-one", "author": "orchestrate", "verifies": "worker"},
            {"id": "check-two", "author": "orchestrate", "verifies": "worker"},
        ]
    )
    assert stages.verify_ids == {"check-one", "check-two"}
    assert stages.parent_of("check-one") == stages.parent_of("check-two") == "worker"


def test_verifies_cycle_is_accepted() -> None:
    """Finding, suspected spec gap: two stages can verify each other.

    ``parse`` refuses only self-verification and an undeclared target, so a cycle is
    legal. Neither stage can then ever be scaffolded (each needs the other earlier in
    the requested list, and both cannot be first), and SPEC section 7.3 has no base
    commit for either, so the set is accepted and unusable. Recorded rather than
    asserted as a refusal, because SPEC section 3.1's refusal list does not name it.
    """
    stages = stageset.parse(
        [
            {"id": "one", "author": "orchestrate", "verifies": "two"},
            {"id": "two", "author": "orchestrate", "verifies": "one"},
        ]
    )
    assert stages.parent_of("one") == "two" and stages.parent_of("two") == "one"


def test_an_empty_declared_stage_set_is_accepted(tmp_path: Path) -> None:
    """Finding, minor: ``stage = []`` declares a hub where nothing can run.

    ``config._load_file`` treats the key's presence as a declaration, so the research
    default is not used, and every later refusal ends in an empty list: ``'impl' is not
    a declared stage; declared: ``. ``control.until`` is emptied as well, so
    ``control.default = "until"`` then fails with ``unknown until stage ;``. Recorded,
    not asserted as a refusal: SPEC section 3.1 does not list an empty set among what is
    refused.
    """
    hub = tmp_path / "hub"
    (hub / ".agents").mkdir(parents=True)
    (hub / ".agents" / "workflow.toml").write_text("stage = []\n[control]\ndefault = \"until\"\n")
    cfg = config_mod.load(hub)
    assert len(cfg.stages) == 0
    assert cfg.control.until == ""
    with pytest.raises(KeyError) as caught:
        cfg.stages.get("impl")
    assert caught.value.args[0].endswith("declared: ")


def test_control_until_is_not_validated_at_load(tmp_path: Path) -> None:
    """Verdict, no defect found: the asymmetry with ``stop_at`` is what SPEC 11 asks for.

    ``config._load_file`` refuses a ``stop_at`` entry that is not a declared stage, so a
    typo there is caught when the file is read, while a typo in ``until`` loads fine and
    is refused later by ``control.validate_until``. That looked like an oversight and is
    not: SPEC section 11 ends the ``control.default`` bullet with "These value checks
    live in the control module, not in config loading". Both paths refuse with a message
    naming the declared ids.
    """
    hub = tmp_path / "hub"
    (hub / ".agents").mkdir(parents=True)
    (hub / ".agents" / "workflow.toml").write_text(
        '[[stage]]\nid = "work"\nauthor = "orchestrate"\n[control]\nuntil = "typo"\n'
    )
    cfg = config_mod.load(hub)
    assert cfg.control.until == "typo"
    with pytest.raises(control_mod.ControlError, match="unknown until stage typo"):
        control_mod.validate_until(beads_mod.FakeBeads([]), "u1", "typo", stages=cfg.stages)


def test_ownership_none_still_rejects_confidential_and_the_memory_export() -> None:
    """Verdict, no defect found: ``none`` means any *ownable* path, not any path.

    SPEC section 3.1 says the memory export directory and ``project.confidential`` stay
    rejected under every mode, and ``ownership.check`` orders the tests that way. The
    ``.git`` the same sentence names is not in ``ALWAYS_REJECTED``, which is harmless
    because git never reports a path under it as changed or untracked.
    """
    monkey = pytest.MonkeyPatch()
    monkey.setattr(
        ownership_mod, "changed_paths", lambda *a, **k: ["k.pem", ".helios/memories/m.md", "x"]
    )
    try:
        result = ownership_mod.check(
            worktree=Path("/x"),
            base_commit="HEAD",
            files=[],
            mode="none",
            confidential=("*.pem",),
        )
    finally:
        monkey.undo()
    assert result.rejected == ("k.pem", ".helios/memories/m.md")
    assert result.allowed == ("x",)


def test_a_stage_id_may_be_arbitrarily_long() -> None:
    """Verdict: no length cap, and no consumer that needs one.

    ``_ID_RE`` bounds the alphabet, not the length. A long id becomes a directory name
    under ``skills/`` and a bd label, both of which have their own limits, so the
    refusal would come from bd or the filesystem rather than from helios. A leading
    digit is refused, which is the case the bead asked about.
    """
    long_id = "a" * 300
    assert stageset.parse([{"id": long_id, "author": "orchestrate"}]).ids == (long_id,)
    with pytest.raises(stageset.StageSetError, match="id must match"):
        stageset.parse([{"id": "1st", "author": "orchestrate"}])
