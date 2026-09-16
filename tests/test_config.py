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


# --- project.check (SPEC section 5, hel-7br) ---------------------------------


def test_check_entries_load_in_order(tmp_path: Path) -> None:
    loaded = cfg.load(
        write_workflow(
            tmp_path,
            "[[project.check]]\nname = 'lint'\ncommand = 'ruff check'\nwhen = 'run'\n"
            "[[project.check]]\nname = 'typecheck'\ncommand = 'pyright'\n",
        )
    )
    assert loaded.project.check == (
        cfg.CheckEntry(name="lint", command="ruff check", when="run"),
        cfg.CheckEntry(name="typecheck", command="pyright", when="both"),
    )


def test_check_name_pattern_refused(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match=r"project\.check\[0\]\.name must match \[a-z0-9_-\]\{1,32\}",
    ):
        cfg.load(
            write_workflow(
                tmp_path, "[[project.check]]\nname = 'Bad Name'\ncommand = 'x'\n"
            )
        )


def test_check_duplicate_name_refused(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError, match=r"project\.check\[1\]\.name lint is a duplicate"
    ):
        cfg.load(
            write_workflow(
                tmp_path,
                "[[project.check]]\nname = 'lint'\ncommand = 'a'\n"
                "[[project.check]]\nname = 'lint'\ncommand = 'b'\n",
            )
        )


@pytest.mark.parametrize("name", ["report", "ownership"])
def test_check_reserved_name_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(
        ValueError, match=rf"project\.check\[0\]\.name {name} is reserved"
    ):
        cfg.load(
            write_workflow(
                tmp_path, f"[[project.check]]\nname = '{name}'\ncommand = 'x'\n"
            )
        )


@pytest.mark.parametrize("when", ["run", "both"])
def test_check_test_name_requires_when_merge(tmp_path: Path, when: str) -> None:
    with pytest.raises(
        ValueError,
        match=r'project\.check\[0\] name "test" requires when = "merge"',
    ):
        cfg.load(
            write_workflow(
                tmp_path,
                f"[[project.check]]\nname = 'test'\ncommand = 'x'\nwhen = '{when}'\n",
            )
        )


def test_check_test_name_allowed_with_when_merge(tmp_path: Path) -> None:
    loaded = cfg.load(
        write_workflow(
            tmp_path,
            "[[project.check]]\nname = 'test'\ncommand = 'x'\nwhen = 'merge'\n",
        )
    )
    assert loaded.project.check == (cfg.CheckEntry(name="test", command="x", when="merge"),)


def test_check_bad_when_value_refused(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match=r'project\.check\[0\]\.when must be "run", "merge" or "both"',
    ):
        cfg.load(
            write_workflow(
                tmp_path,
                "[[project.check]]\nname = 'lint'\ncommand = 'x'\nwhen = 'never'\n",
            )
        )


def test_check_unknown_entry_key_named(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"project\.check\[0\]\.nope"):
        cfg.load(
            write_workflow(
                tmp_path, "[[project.check]]\nname = 'lint'\ncommand = 'x'\nnope = 1\n"
            )
        )


def test_check_entry_wrong_type_named(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match=r"project\.check\[0\]\.command"):
        cfg.load(
            write_workflow(
                tmp_path, "[[project.check]]\nname = 'lint'\ncommand = 1\n"
            )
        )


def test_check_missing_required_key_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"project\.check\[0\]\.command"):
        cfg.load(write_workflow(tmp_path, "[[project.check]]\nname = 'lint'\n"))
    with pytest.raises(ValueError, match=r"project\.check\[0\]\.name"):
        cfg.load(write_workflow(tmp_path, "[[project.check]]\ncommand = 'x'\n"))


def test_old_keys_with_check_entry_refused(tmp_path: Path) -> None:
    with pytest.raises(
        ValueError,
        match=r"project\.test and project\.typecheck cannot be set with project\.check",
    ):
        cfg.load(
            write_workflow(
                tmp_path,
                "[project]\ntest = 'pytest'\n"
                "[[project.check]]\nname = 'lint'\ncommand = 'x'\n",
            )
        )
    with pytest.raises(
        ValueError,
        match=r"project\.test and project\.typecheck cannot be set with project\.check",
    ):
        cfg.load(
            write_workflow(
                tmp_path,
                "[project]\ntypecheck = 'pyright'\n"
                "[[project.check]]\nname = 'lint'\ncommand = 'x'\n",
            )
        )


def test_effective_checks_sugar_reproduces_defaults(tmp_path: Path) -> None:
    loaded = cfg.load(tmp_path)
    assert cfg.effective_checks(loaded.project) == (
        cfg.CheckEntry(name="test", command="uv run pytest -q", when="merge"),
        cfg.CheckEntry(name="typecheck", command="", when="both"),
    )


def test_effective_checks_sugar_from_configured_old_keys(tmp_path: Path) -> None:
    loaded = cfg.load(
        write_workflow(
            tmp_path, "[project]\ntest = 'make test'\ntypecheck = 'make typecheck'\n"
        )
    )
    assert cfg.effective_checks(loaded.project) == (
        cfg.CheckEntry(name="test", command="make test", when="merge"),
        cfg.CheckEntry(name="typecheck", command="make typecheck", when="both"),
    )


def test_effective_checks_uses_check_entries_when_present(tmp_path: Path) -> None:
    loaded = cfg.load(
        write_workflow(
            tmp_path,
            "[[project.check]]\nname = 'lint'\ncommand = 'ruff'\nwhen = 'run'\n",
        )
    )
    assert cfg.effective_checks(loaded.project) == (
        cfg.CheckEntry(name="lint", command="ruff", when="run"),
    )


def test_check_empty_command_still_an_entry(tmp_path: Path) -> None:
    loaded = cfg.load(
        write_workflow(
            tmp_path, "[[project.check]]\nname = 'lint'\ncommand = ''\n"
        )
    )
    assert loaded.project.check == (cfg.CheckEntry(name="lint", command="", when="both"),)


def test_telemetry_defaults(tmp_path: Path) -> None:
    loaded = cfg.load(tmp_path)
    assert loaded.telemetry == cfg.TelemetryConfig()
    assert loaded.telemetry.enabled is False
    assert loaded.telemetry.endpoint == ""
    assert loaded.telemetry.timeout_s == 5
    assert loaded.telemetry.service_name == "helios"


def test_telemetry_enabled_requires_endpoint(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"telemetry\.enabled requires telemetry\.endpoint"):
        cfg.load(write_workflow(tmp_path, "[telemetry]\nenabled = true\n"))
    with pytest.raises(ValueError, match=r"telemetry\.enabled requires telemetry\.endpoint"):
        cfg.load(write_workflow(tmp_path, "[telemetry]\nenabled = true\nendpoint = ''\n"))


def test_telemetry_enabled_with_endpoint_is_accepted(tmp_path: Path) -> None:
    loaded = cfg.load(
        write_workflow(
            tmp_path,
            "[telemetry]\nenabled = true\nendpoint = 'http://localhost:4318/v1/traces'\n",
        )
    )
    assert loaded.telemetry.enabled is True
    assert loaded.telemetry.endpoint == "http://localhost:4318/v1/traces"


def test_telemetry_unknown_key_names_the_dotted_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"telemetry\.nope"):
        cfg.load(write_workflow(tmp_path, "[telemetry]\nnope = 1\n"))


def test_telemetry_wrong_typed_values_name_the_dotted_key(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match=r"telemetry\.enabled"):
        cfg.load(write_workflow(tmp_path, "[telemetry]\nenabled = 'yes'\n"))
    with pytest.raises(TypeError, match=r"telemetry\.enabled"):
        # An int is not a bool, even though bool is an int subclass in Python.
        cfg.load(write_workflow(tmp_path, "[telemetry]\nenabled = 1\n"))
    with pytest.raises(TypeError, match=r"telemetry\.endpoint"):
        cfg.load(write_workflow(tmp_path, "[telemetry]\nendpoint = 1\n"))
    with pytest.raises(TypeError, match=r"telemetry\.timeout_s"):
        cfg.load(write_workflow(tmp_path, "[telemetry]\ntimeout_s = 'x'\n"))
    with pytest.raises(TypeError, match=r"telemetry\.service_name"):
        cfg.load(write_workflow(tmp_path, "[telemetry]\nservice_name = 1\n"))
