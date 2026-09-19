"""Stage set tests (SPEC section 3): SPEC/shipped-set parity, StageSet.parent_of, and the
layer boundary between the dispatch core and the research pack.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from helios import stageset

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "docs" / "SPEC.md"


def _spec_stage_ids() -> list[str]:
    """Parse the `id` column of the section 3 stages table in docs/SPEC.md, in row order.

    Section 3 now has two subsections, "3.1 Declaring a stage set" and "3.2 The shipped
    research stage set", each a level-3 heading (`### `); the ten stage rows live under 3.2.
    The split below still cuts on the next level-2 heading (`\\n## `), which "### 3.1" and
    "### 3.2" do not match (a third `#` follows where the pattern needs a space), so the
    captured text still runs through both subsections to "## 4. Result contracts". Confirmed
    against the current file rather than assumed: 3.1's own fenced example carries an `id =
    "impl"` and an `id = "verify-code"` line, neither of which matches the table row pattern
    below (`| `id`` |`, backtick-quoted in a pipe table), so it cannot leak into this list.
    """
    text = SPEC.read_text()
    section = text.split("## 3. Stages", 1)[1].split("\n## ", 1)[0]
    assert "### 3.1" in section and "### 3.2" in section, "SPEC section 3 lost its subsections"
    ids = []
    for line in section.splitlines():
        match = re.match(r"\|\s*`([a-z-]+)`\s*\|", line)
        if match:
            ids.append(match.group(1))
    return ids


def test_shipped_research_set_matches_the_spec_section_3_2_table() -> None:
    """The shipped declaration and the SPEC table describe the same ten stages.

    This is the parity check the old `test_stages_match_spec_table` held for the deleted
    `STAGES` tuple: the two are written independently (one in prose, one in TOML) and must not
    drift, so this fails if either changes without the other.
    """
    assert list(stageset.research().ids) == _spec_stage_ids()


# ---------------------------------------------------------------------------
# StageSet.parent_of, which replaced the deleted module-level `parent_stage`.
# ---------------------------------------------------------------------------


def test_parent_of_names_the_stage_each_research_verify_stage_checks() -> None:
    stages = stageset.research()
    assert stages.parent_of("verify-math") == "model"
    assert stages.parent_of("verify-code") == "impl"
    assert stages.parent_of("verify-validate") == "validate"


def test_parent_of_is_none_for_a_stage_that_verifies_nothing() -> None:
    stages = stageset.research()
    for stage_id in ("frame", "survey", "model", "impl", "validate", "report", "remember"):
        assert stages.parent_of(stage_id) is None


def test_parent_of_raises_for_an_undeclared_stage() -> None:
    stages = stageset.research()
    try:
        stages.parent_of("nonesuch")
    except KeyError as exc:
        assert "declared" in exc.args[0]
    else:
        raise AssertionError("parent_of('nonesuch') did not raise")


# ---------------------------------------------------------------------------
# Layer boundary (SPEC section 3, "Core and pack"): no module of the dispatch core names a
# research stage id, whether hardcoded whole, split across two literals Python concatenates,
# folded into a multi-word literal ("frame model report".split()), or tested by a
# startswith/endswith prefix shape (the branch family the refactor deleted). PACK below is the
# research pack this excludes from CORE_MODULES: the section 3.2 declaration, the program and
# claims modules of SPEC section 15 and the commands over them, and src/helios/stageset.py,
# which is neither core nor pack but the schema stage ids are checked against (REQUIREMENTS,
# GATES, OWNERSHIP_MODES), so it legitimately spells "model" and "report" as vocabulary
# entries; the brief for this bead also says not to edit it.
# ---------------------------------------------------------------------------

RESEARCH_STAGE_IDS = (
    "frame",
    "survey",
    "model",
    "verify-math",
    "impl",
    "verify-code",
    "validate",
    "verify-validate",
    "report",
    "remember",
)

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


def _core_modules_from_disk() -> tuple[str, ...]:
    """Every module under src/helios that is not in PACK.

    Derived from the filesystem rather than hand-listed, so a new core module (this test's
    own finding: src/helios/__init__.py was missing from the old hand list) is covered from
    the moment it exists rather than on whoever remembers to add it here.
    """
    return tuple(
        str(path.relative_to(REPO))
        for path in sorted((REPO / "src" / "helios").rglob("*.py"))
        if str(path.relative_to(REPO)) not in PACK
    )


CORE_MODULES = _core_modules_from_disk()


def _string_constants(text: str) -> list[tuple[int, str]]:
    """Every string literal in a module's source except a docstring, with its line number.

    ``ast`` joins adjacent literals into one constant, so this sees an id Python
    concatenates from two source literals, which a line-based regex cannot. A docstring is
    a string that is the whole of an expression statement; prose is not a hardcoded stage
    id and would drown the signal.
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
    """The stage ids a string literal hardcodes.

    Two forms count: the literal is exactly one stage id, or every whitespace separated
    word of it is a stage id and there are at least two (the ``"frame model
    report".split()`` form). An English sentence that happens to contain "report" or
    "model" fails both, so neither needs an exclusion.
    """
    words = value.split()
    if len(words) == 1 and words[0] == value and value in RESEARCH_STAGE_IDS:
        return {value}
    if len(words) > 1 and all(word in RESEARCH_STAGE_IDS for word in words):
        return set(words)
    return set()


# Reviewed exceptions, keyed by the exact source line so an unrelated edit elsewhere cannot
# silently widen the exemption, and an edit to one of these lines forces a fresh look here.
# Every one draws the word from a vocabulary SPEC section 3.1 or section 21 actually defines,
# except the last two, which are bd's own verb and cannot be made precise; see the report.
ALLOWED_LINES: dict[str, str] = {
    # config.py: an LLM model name (the `[harness.*]` table) and the default agent role named
    # "model" (SPEC section 5) -- a harness field and a role, neither a stage id.
    '"model": _OPT_STR,': "harness config field `model` (LLM model name), not a stage id",
    '"model": "claude",': "default agent role named `model`, not a stage id",
    'return self.roles.get("model", DEFAULT_ROLES["model"])':
        "agent role lookup for the role named `model`, not a stage id",
    # config.py: reserved check names (SPEC section 5); "report" here is the check that
    # complains about a bad AgentReport, the same check as run.py's Check(name="report") below.
    '_CHECK_RESERVED_NAMES = ("report", "ownership")': "check names, not stage ids",
    # config.py: SPEC section 5's own configuration table states these two defaults by name --
    # "control.until" and "control.stop_at" take the section 3.2 stages when a hub declares no
    # stages of its own, and config._load_file empties both the moment a hub declares any
    # (grep for "The research defaults name research stages" a few lines above each). This is
    # the one place the shipped research pack's defaults are data inside a core module instead
    # of inside stagesets/research.toml, and test_stageset.py holds them to that SPEC table row
    # for row, the same parity duty this file holds for the stage declarations themselves.
    'until: str = "verify-code"': "SPEC section 5 default for the shipped research pack",
    'stop_at: tuple[str, ...] = ("frame", "model", "report")':
        "SPEC section 5 default for the shipped research pack",
    # control.py: `gate` (SPEC section 3.1) is "report", "verdict" or "none" -- a judging mode,
    # not a stage id; it names one of the ten by coincidence.
    'if stage.gate == "report" and report.status != WorkStatus.DONE:':
        "gate value `report`, not a stage id",
    # preflight.py: `requires` (SPEC section 3.1) may list "model", meaning the bead needs a
    # substantive `## Model` section -- a requirement name, not a stage id.
    'if stage.demands("model") and bead.unit:': "requirement name `model`, not a stage id",
    # run.py: telemetry span and attribute names (SPEC section 21). "validate" names the
    # classify phase every attempt goes through regardless of stage; "model" carries the
    # harness's LLM model name.
    'telemetry_mod.Span(name="validate", start=validate_started_at, end=validate_finished_at)':
        "telemetry span for the classify phase every attempt has, not a stage id",
    'attributes["model"] = model': "the harness's LLM model name, not a stage id",
    'envelope_mod.Check(name="report", passed=False, detail=problem)':
        "check name for a bad report, not a stage id",
    # beads.py: bd's own `remember`/`recall` verbs (SPEC section 13) exist for a bead of any
    # stage, not only the "remember" stage. Unlike gate/ownership/requires above, there is no
    # separate vocabulary bd draws this from: its memory command is just spelled the same as
    # the stage id, and this test cannot tell the two apart on the text alone.
    'self._run(["remember", "--key", key, "--", value])': "bd's memory verb, not a stage id",
    'self.argv_log.append(["remember", "--key", key, value])':
        "bd's memory verb, not a stage id",
    # harness/fake.py: the fake harness script's "report" key holds the AgentReport JSON a
    # test wants written out (SPEC section 4.1) -- a field name of the result contract, not a
    # stage id, the same field envelope.py's own "report" field names. The illustration of
    # this in the module docstring needs no entry here: _string_constants skips docstrings.
    'report = script.get("report", None)':
        "AgentReport field name in the fake harness script, not a stage id",
}


def test_core_modules_name_no_research_stage_id() -> None:
    """No core module hardcodes a stage id: whole, concatenated across adjacent literals, or
    folded into a multi-word literal like ``"frame model report".split()``.
    """
    violations = []
    for rel in CORE_MODULES:
        text = (REPO / rel).read_text()
        lines = text.splitlines()
        for lineno, value in _string_constants(text):
            hits = _hardcoded_ids(value)
            if not hits:
                continue
            if lines[lineno - 1].strip() in ALLOWED_LINES:
                continue
            violations.append(f"{rel}:{lineno}: names {sorted(hits)!r}: {lines[lineno - 1].strip()}")
    assert not violations, "\n".join(violations)


def test_core_modules_test_no_kind_against_a_stage_id_prefix() -> None:
    """No core module tests a kind or stage against a piece of a research stage id by shape,
    such as ``kind.startswith("verify")``, the branch family the refactor deleted. Neither
    the whole-literal rule above nor a line-based one can see this: "verify" is not itself a
    stage id, only a prefix several of them share.
    """
    violations = []
    pieces = {
        (stage_id[:n], stage_id)
        for stage_id in RESEARCH_STAGE_IDS
        for n in range(1, len(stage_id) + 1)
    } | {
        (stage_id[-n:], stage_id)
        for stage_id in RESEARCH_STAGE_IDS
        for n in range(1, len(stage_id) + 1)
    }
    for rel in CORE_MODULES:
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


def test_every_allowed_line_still_exists() -> None:
    """Guard the guard: a stale exemption (the line it names is gone) must be dropped, not left
    to silently cover whatever now happens to be at that spot.
    """
    texts = [(rel, (REPO / rel).read_text()) for rel in CORE_MODULES]
    for stripped in ALLOWED_LINES:
        assert any(stripped in text for _, text in texts), f"stale exemption: {stripped!r}"
