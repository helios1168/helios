"""Stage set tests (SPEC §3.1, §3.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from helios import config as cfg
from helios import stageset

RESEARCH_IDS = (
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


def write_workflow(tmp_path: Path, body: str) -> Path:
    (tmp_path / ".agents").mkdir(exist_ok=True)
    (tmp_path / ".agents" / "workflow.toml").write_text(body)
    return tmp_path


# ---------------------------------------------------------------------------
# The shipped research set.
# ---------------------------------------------------------------------------


def test_research_set_is_the_ten_stages_in_order() -> None:
    assert stageset.research().ids == RESEARCH_IDS


def test_research_set_carries_the_fields_of_spec_3_2() -> None:
    stages = stageset.research()
    assert stages.verify_ids == {"verify-math", "verify-code", "verify-validate"}
    assert stages.get("impl").requires == {"files", "test"}
    assert stages.get("verify-math").requires == {"unit", "parent", "model"}
    assert [s.id for s in stages if s.gate == "none"] == [
        "frame",
        "survey",
        "model",
        "report",
        "remember",
    ]
    assert [s.id for s in stages if s.ownership == "artifacts"] == [
        "verify-math",
        "verify-code",
        "verify-validate",
    ]
    assert stages.parent_of("verify-code") == "impl"
    assert stages.parent_of("impl") is None
    # remember is the one stage unit new does not offer, and it says so with a field.
    assert stages.scaffold_ids == RESEARCH_IDS[:-1]


def test_a_hub_declaring_nothing_gets_the_research_set(tmp_path: Path) -> None:
    assert cfg.load(tmp_path).stages.ids == RESEARCH_IDS
    hub = write_workflow(tmp_path, "[project]\ntest = 'pytest'\n")
    assert cfg.load(hub).stages.ids == RESEARCH_IDS


# ---------------------------------------------------------------------------
# A hub declaring its own stages.
# ---------------------------------------------------------------------------


GENERAL = """
[[stage]]
id = "task"
author = "implement"
gate = "report"
ownership = "none"
"""


def test_a_declared_set_replaces_the_research_set(tmp_path: Path) -> None:
    stages = cfg.load(write_workflow(tmp_path, GENERAL)).stages
    assert stages.ids == ("task",)
    task = stages.get("task")
    assert task.requires == frozenset()
    assert task.ownership == "none"
    assert task.scaffold is True
    assert stages.verify_ids == frozenset()


def test_a_declared_set_empties_the_research_control_defaults(tmp_path: Path) -> None:
    control = cfg.load(write_workflow(tmp_path, GENERAL)).control
    assert control.until == ""
    assert control.stop_at == ()


def test_control_defaults_stay_with_the_research_set(tmp_path: Path) -> None:
    control = cfg.load(write_workflow(tmp_path, "[control]\ndefault = 'auto'\n")).control
    assert control.until == "verify-code"
    assert control.stop_at == ("frame", "model", "report")


def test_declared_control_values_win(tmp_path: Path) -> None:
    body = GENERAL + "\n[control]\nuntil = 'task'\nstop_at = ['task']\n"
    control = cfg.load(write_workflow(tmp_path, body)).control
    assert control.until == "task"
    assert control.stop_at == ("task",)


def test_declaration_order_is_stage_order(tmp_path: Path) -> None:
    body = """
[[stage]]
id = "second"
author = "implement"

[[stage]]
id = "first"
author = "implement"
"""
    stages = cfg.load(write_workflow(tmp_path, body)).stages
    assert stages.ids == ("second", "first")
    assert stages.index("first") == 1


# ---------------------------------------------------------------------------
# Refusals (SPEC §3.1). Each is a ConfigError, so the command layer exits 2.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "names"),
    [
        ("[[stage]]\nid = 'a'\nauthor = 'implement'\n\n[[stage]]\nid = 'a'\nauthor = 'implement'\n",
         "stage a is declared twice"),
        ("[[stage]]\nid = 'A'\nauthor = 'implement'\n", "id must match"),
        ("[[stage]]\nauthor = 'implement'\n", "needs an `id`"),
        ("[[stage]]\nid = 'a'\n", "needs an `author`"),
        ("[[stage]]\nid = 'a'\nauthor = 'implement'\nrequires = ['nonesuch']\n",
         "requires 'nonesuch'"),
        ("[[stage]]\nid = 'a'\nauthor = 'implement'\ngate = 'nonesuch'\n", "gate must be one of"),
        ("[[stage]]\nid = 'a'\nauthor = 'implement'\nownership = 'nonesuch'\n",
         "ownership must be one of"),
        ("[[stage]]\nid = 'a'\nauthor = 'implement'\nverifies = 'nonesuch'\n",
         "verifies nonesuch, which is not declared"),
        ("[[stage]]\nid = 'a'\nauthor = 'implement'\nverifies = 'a'\n", "verifies itself"),
        ("[[stage]]\nid = 'a'\nauthor = 'other'\n", "needs a `verifies`"),
        ("[[stage]]\nid = 'a'\nauthor = 'nonesuch'\n", "is not a key of [agents]"),
        ("[[stage]]\nid = 'a'\nauthor = 'implement'\nnonesuch = 1\n", "unknown key 'nonesuch'"),
        ("[[stage]]\nid = 'a'\nauthor = 'implement'\nscaffold = 'yes'\n",
         "scaffold must be true or false"),
        (GENERAL + "\n[control]\nstop_at = ['nonesuch']\n", "control.stop_at names 'nonesuch'"),
    ],
)
def test_a_bad_stage_set_is_refused(tmp_path: Path, body: str, names: str) -> None:
    with pytest.raises(cfg.ConfigError) as caught:
        cfg.load(write_workflow(tmp_path, body))
    assert names in str(caught.value)


def test_an_author_naming_a_declared_role_is_accepted(tmp_path: Path) -> None:
    body = """
[agents]
reviewer = "claude"

[[stage]]
id = "review"
author = "reviewer"
"""
    loaded = cfg.load(write_workflow(tmp_path, body))
    assert loaded.agents.spec("reviewer") == "claude"
    assert cfg.spec_for_kind(loaded, "review") == "claude"


def test_the_documented_roles_keep_their_defaults(tmp_path: Path) -> None:
    agents = cfg.load(write_workflow(tmp_path, "[agents]\nreviewer = 'agy'\n")).agents
    assert agents.implement == "codex"
    assert agents.verify_code == "other"
    assert agents.spec("reviewer") == "agy"
    assert agents.verify_order == ("claude", "codex", "opencode", "agy")


def test_an_undeclared_role_is_refused_when_a_stage_names_it() -> None:
    agents = cfg.AgentsConfig()
    with pytest.raises(KeyError, match="declared"):
        agents.spec("nonesuch")


def test_spec_for_kind_reads_the_stage_author(tmp_path: Path) -> None:
    loaded = cfg.load(write_workflow(tmp_path, GENERAL))
    assert cfg.spec_for_kind(loaded, "task") == "codex"
    with pytest.raises(ValueError, match="unknown bead kind"):
        cfg.spec_for_kind(loaded, "impl")


def test_get_and_index_name_the_declared_ids(tmp_path: Path) -> None:
    stages = cfg.load(write_workflow(tmp_path, GENERAL)).stages
    with pytest.raises(KeyError, match="declared: task"):
        stages.get("impl")
    with pytest.raises(KeyError, match="declared: task"):
        stages.index("impl")
    assert stages.find("impl") is None
