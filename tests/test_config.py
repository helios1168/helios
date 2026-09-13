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
    assert cfg.resolve_harness("codex", author="claude", verify_order=order) == "codex"
    harness, profile = cfg.split_spec("opencode:ci")
    assert (harness, profile) == ("opencode", "ci")
