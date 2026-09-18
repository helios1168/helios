"""Tests for helios.templates.path and for the templates it ships."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from helios import config
from helios.templates import path


def test_path_finds_existing_template() -> None:
    result = path("workflow.toml")
    assert result.is_file()
    assert result.name == "workflow.toml"


def test_path_raises_for_missing_template() -> None:
    with pytest.raises(FileNotFoundError, match="does-not-exist.toml"):
        path("does-not-exist.toml")


def copy_template_into_hub(hub: Path, *, damage: tuple[str, str] | None = None) -> Path:
    """Install templates/workflow.toml as a hub's own config, optionally damaging one line."""
    (hub / ".agents").mkdir()
    target = hub / ".agents" / "workflow.toml"
    shutil.copyfile(path("workflow.toml"), target)
    if damage is not None:
        old, new = damage
        body = target.read_text()
        assert old in body, f"template no longer contains {old!r}"
        target.write_text(body.replace(old, new, 1))
    return hub


def test_shipped_workflow_template_parses_to_the_defaults(tmp_path: Path) -> None:
    """The template documents the defaults by showing them, so it must load as the defaults.

    Without this, the shipped template is the one configuration file no test reads, and a default
    changed in config.py but not here would fail in a user's hub instead of in CI.
    """
    hub = copy_template_into_hub(tmp_path)
    loaded = config.load(hub)
    default = config.Config(hub=hub)

    assert loaded.hub == hub
    assert loaded.project == default.project
    assert loaded.agents == default.agents
    assert loaded.control == default.control
    assert loaded.memory == default.memory
    assert loaded.telemetry == default.telemetry
    assert loaded.tolerance == default.tolerance
    # The template names the four known harnesses as empty tables, which the loader reads as a
    # HarnessConfig holding nothing but defaults. A bare Config names no harness at all, so the
    # comparison here is per entry rather than against default.harness.
    assert sorted(loaded.harness) == ["agy", "claude", "codex", "opencode"]
    assert all(entry == config.HarnessConfig() for entry in loaded.harness.values())


@pytest.mark.parametrize(
    ("damage", "names"),
    [
        (("[telemetry]", "[nonesuch]"), "nonesuch"),
        (("inject_cap_bytes = 32000", 'inject_cap_bytes = "big"'), "memory.inject_cap_bytes"),
    ],
    ids=["unknown-key", "wrong-type"],
)
def test_the_template_test_fails_when_the_template_goes_wrong(
    tmp_path: Path, damage: tuple[str, str], names: str
) -> None:
    """Guard the guard: an unknown key or a wrong type in the template has to be a refusal."""
    hub = copy_template_into_hub(tmp_path, damage=damage)
    with pytest.raises(config.ConfigError, match=re.escape(names)):
        config.load(hub)
