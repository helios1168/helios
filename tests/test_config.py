"""Config loader tests (SPEC §5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from helios import config as cfg


def test_defaults_without_workflow_file(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    loaded = cfg.load(tmp_path)
    assert loaded.hub == tmp_path.resolve()
    assert loaded.project.test == "uv run pytest -q"
    assert loaded.project.always_allowed == ("tests/",)
    assert loaded.agents.verify_code == "other"
    assert loaded.agents.verify_order == ("claude", "codex", "opencode", "agy")
    assert loaded.memory.inject_cap_bytes == 32000


def test_hub_walk_prefers_workflow_over_git(tmp_path: Path) -> None:
    hub = tmp_path / "proj"
    (hub / ".git").mkdir(parents=True)
    (hub / ".agents").mkdir()
    (hub / ".agents" / "workflow.toml").write_text('[project]\ntest = "make check"\n')
    deep = hub / "a" / "b"
    deep.mkdir(parents=True)
    loaded = cfg.load(deep)
    assert loaded.hub == hub.resolve()
    assert loaded.project.test == "make check"


def test_unknown_key_names_the_key(tmp_path: Path) -> None:
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "workflow.toml").write_text("[project]\nnope = 1\n")
    with pytest.raises(ValueError, match="nope"):
        cfg.load(tmp_path)


def test_other_resolves_over_verify_order() -> None:
    order = ("claude", "codex", "opencode", "agy")
    assert cfg.resolve_harness("other", author="codex", verify_order=order) == "claude"
    assert cfg.resolve_harness("other", author="claude", verify_order=order) == "codex"
    assert cfg.resolve_harness("other", author="claude:opus", verify_order=order) == "codex"
    assert cfg.resolve_harness("other", author=None, verify_order=order) == "claude"
    assert cfg.resolve_harness("codex", author="claude", verify_order=order) == "codex"
    harness, profile = cfg.split_spec("opencode:ci")
    assert (harness, profile) == ("opencode", "ci")


def write_workflow(tmp_path: Path, text: str) -> Path:
    (tmp_path / ".agents").mkdir(exist_ok=True)
    path = tmp_path / ".agents" / "workflow.toml"
    path.write_text(text)
    return tmp_path


def test_wrong_typed_values_name_the_dotted_key(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match=r"tolerance\.a"):
        cfg.load(write_workflow(tmp_path, "[tolerance]\na = 'x'\n"))
    with pytest.raises(TypeError, match=r"project\.test"):
        cfg.load(write_workflow(tmp_path, "[project]\ntest = 1\n"))
    with pytest.raises(TypeError, match=r"memory\.inject_cap_bytes"):
        cfg.load(write_workflow(tmp_path, "[memory]\ninject_cap_bytes = 'big'\n"))


def test_unknown_nested_key_names_the_dotted_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"harness\.codex\.nope"):
        cfg.load(write_workflow(tmp_path, "[harness.codex]\nnope = 1\n"))


def test_table_value_with_wrong_type_names_the_key(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match=r"'control'"):
        cfg.load(write_workflow(tmp_path, "control = 1\n"))
    with pytest.raises(TypeError, match=r"'memory'"):
        cfg.load(write_workflow(tmp_path, 'memory = "abc"\n'))


def test_server_url_is_opencode_only(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"harness\.codex\.server_url"):
        cfg.load(write_workflow(tmp_path, '[harness.codex]\nserver_url = "x"\n'))
    loaded = cfg.load(write_workflow(tmp_path, '[harness.opencode]\nserver_url = "x"\n'))
    assert loaded.harness["opencode"].server_url == "x"


def test_verify_validate_has_its_own_spec(tmp_path: Path) -> None:
    assert cfg.load(tmp_path).agents.verify_validate == "other"
    loaded = cfg.load(write_workflow(tmp_path, "[agents]\nverify_validate = 'codex'\n"))
    assert cfg.spec_for_kind(loaded, "verify-validate") == "codex"
    assert cfg.harness_for_kind(loaded, "verify-validate", author="claude") == "codex"
