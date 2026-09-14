"""Tests for helios.templates.path."""

from __future__ import annotations

import pytest

from helios.templates import path


def test_path_finds_existing_template() -> None:
    result = path("workflow.toml")
    assert result.is_file()
    assert result.name == "workflow.toml"


def test_path_raises_for_missing_template() -> None:
    with pytest.raises(FileNotFoundError, match="does-not-exist.toml"):
        path("does-not-exist.toml")
