"""Tests for `helios unit new` (SPEC §10.2)."""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import threading
from argparse import Namespace
from pathlib import Path
from typing import Any, cast

import pytest

from helios import beads as beads_mod
from helios import cli
from helios.beads import Bead, FakeBeads
from helios.commands import unit_new
from helios.config import AgentsConfig, Config
from helios.units import (
    StageRow,
    UnitNewError,
    UnitWriteError,
    create_unit,
    format_table,
    unit_lock,
)


def _config(hub: Path, **agents: Any) -> Config:
    return Config(hub=hub, agents=AgentsConfig(**agents))


def _hub(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir(exist_ok=True)
    return tmp_path


def _unit_path(hub: Path, unit: str = "U1") -> Path:
    return hub / "docs" / "units" / f"{unit}.md"


def _run_ok(
    *,
    fake: FakeBeads,
    hub: Path,
    unit: str = "U1",
    title: str = "T",
    stages: str = "model,impl",
    files: str | None = "src/*.py",
    test: str | None = "uv run pytest -q",
    config: Config | None = None,
) -> list[StageRow]:
    return create_unit(
        beads=fake,
        config=config or _config(hub),
        unit=unit,
        title=title,
        stages=stages,
        files=files,
        test=test,
    )


# Accept 1: every step 1 refusal writes nothing.
REFUSALS: list[dict[str, Any]] = [
    {"unit": "bad name", "stages": "model"},
    {"unit": "", "stages": "model"},
    {"unit": "-x", "stages": "model"},
    {"unit": "a/b", "stages": "model"},
    {"unit": "U1", "stages": "model,,impl", "files": "a.py", "test": "t"},
    {"unit": "U1", "stages": "model,model"},
    {"unit": "U1", "stages": "model,bogus"},
    {"unit": "U1", "stages": "model,remember"},
    {"unit": "U1", "stages": "impl,model", "files": "a.py", "test": "t"},
    {"unit": "U1", "stages": "verify-math"},
    {"unit": "U1", "stages": "verify-math,verify-code"},
    {"unit": "U1", "stages": "model,impl", "files": None, "test": "t"},
    {"unit": "U1", "stages": "model,impl", "files": "a.py", "test": None},
    {"unit": "U1", "stages": "model,impl", "files": "a.py", "test": ""},
    {"unit": "U1", "stages": "model,impl", "files": "a.py", "test": "   "},
    {"unit": "U1", "stages": "model,validate", "files": "a.py", "test": None},
    {"unit": "U1", "stages": "impl", "files": "a,,b", "test": "t"},
    {"unit": "U1\n", "stages": "model"},
    {"unit": "a\n", "stages": "model"},
    {"unit": "a" * 101, "stages": "model"},
    {"unit": "a" * 300, "stages": "model"},
]


@pytest.mark.parametrize("case", REFUSALS)
def test_validation_refusals_write_nothing(tmp_path: Path, case: dict[str, Any]) -> None:
    hub = _hub(tmp_path)
    fake = FakeBeads()
    with pytest.raises(UnitNewError):
        create_unit(
            beads=fake,
            config=_config(hub),
            unit=case["unit"],
            title="T",
            stages=case["stages"],
            files=case.get("files"),
            test=case.get("test"),
        )
    assert fake.argv_log == []
    assert not _unit_path(hub).exists()


def test_existing_unit_file_refuses_without_writing(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    path = _unit_path(hub)
    path.parent.mkdir(parents=True)
    path.write_text("kept\n", encoding="utf-8")
    fake = FakeBeads()
    with pytest.raises(UnitNewError, match="already exists"):
        _run_ok(fake=fake, hub=hub, stages="model", files=None, test=None)
    assert fake.argv_log == []
    assert path.read_text(encoding="utf-8") == "kept\n"


def test_verify_without_earlier_stage_message(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    with pytest.raises(
        UnitNewError, match=r"^verify-code has no earlier stage to verify$"
    ):
        _run_ok(
            fake=FakeBeads(), hub=hub, stages="verify-code", files=None, test=None
        )


def test_command_refusal_exits_2_with_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = _hub(tmp_path)
    monkeypatch.chdir(hub)
    fake = FakeBeads()
    monkeypatch.setattr(beads_mod, "Beads", lambda cwd: fake)
    args = Namespace(
        unit="bad name", title="T", stages="model", files=None, test=None
    )
    assert unit_new.run(args) == 2
    err = capsys.readouterr().err
    assert err.startswith("helios: ")
    assert err.strip() != ""
    assert fake.argv_log == []
    assert not _unit_path(hub).exists()


def test_config_error_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = _hub(tmp_path)
    monkeypatch.chdir(hub)
    (hub / ".agents").mkdir()
    (hub / ".agents" / "workflow.toml").write_text("[project]\ntest = 3\n")
    args = Namespace(unit="U1", title="T", stages="model", files=None, test=None)
    assert unit_new.run(args) == 2
    assert capsys.readouterr().err.startswith("helios: ")


# Accept 2: authors per step 3.
def test_authors_for_every_stage(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    rows = _run_ok(
        fake=FakeBeads(),
        hub=hub,
        stages=",".join(
            [
                "frame",
                "survey",
                "model",
                "verify-math",
                "impl",
                "verify-code",
                "validate",
                "verify-validate",
                "report",
            ]
        ),
    )
    assert [row.author for row in rows] == [
        "claude",  # frame: orchestrate
        "claude",  # survey: orchestrate
        "claude",  # model
        "codex",  # verify-math: other, parent model author claude
        "codex",  # impl
        "claude",  # verify-code: other, parent impl author codex
        "codex",  # validate
        "claude",  # verify-validate: other, parent validate author codex
        "claude",  # report: orchestrate
    ]


def test_other_resolves_against_profiled_parent(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    rows = _run_ok(
        fake=FakeBeads(),
        hub=hub,
        stages="model,verify-math",
        files=None,
        test=None,
        config=_config(hub, model="claude:opus"),
    )
    assert [row.author for row in rows] == ["claude:opus", "codex"]
    assert ":" not in rows[1].author


# Accept 3: titles, labels and JSON-typed metadata.
def test_bead_title_labels_and_metadata_types(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    fake = FakeBeads()
    _run_ok(fake=fake, hub=hub, unit="U1", title="Big Title")
    creates = [entry for entry in fake.argv_log if entry[0] == "create"]
    assert [entry[1] for entry in creates] == [
        "U1 model: Big Title",
        "U1 impl: Big Title",
    ]
    assert creates[0][2] == ["unit:U1", "kind:model"]
    assert creates[1][2] == ["unit:U1", "kind:impl"]
    meta0 = cast(dict[str, Any], creates[0][3])
    meta1 = cast(dict[str, Any], creates[1][3])
    assert meta0 == {"unit": "U1", "kind": "model", "author": "claude"}
    assert meta1 == {
        "unit": "U1",
        "kind": "impl",
        "author": "codex",
        "files": ["src/*.py"],
        "test": "uv run pytest -q",
    }
    assert isinstance(meta1["files"], list)
    assert isinstance(meta1["test"], str)


# Accept 4: chain and parent.
def test_chain_and_verify_parent(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    fake = FakeBeads()
    rows = _run_ok(
        fake=fake,
        hub=hub,
        stages="model,verify-math,impl,verify-code",
    )
    beads = [row.bead for row in rows]
    assert beads == ["fake-1", "fake-2", "fake-3", "fake-4"]
    deps = [entry for entry in fake.argv_log if entry[0] == "dep"]
    assert deps == [
        ["dep", "add", "fake-2", "fake-1"],
        ["dep", "add", "fake-3", "fake-2"],
        ["dep", "add", "fake-4", "fake-3"],
    ]
    assert fake.show("fake-2").metadata["parent"] == "fake-1"
    assert fake.show("fake-4").metadata["parent"] == "fake-3"
    assert [row.blocked_by for row in rows] == [None, "fake-1", "fake-2", "fake-3"]


def test_reuse_open_repairs_parent_and_skips_closed(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    fake = FakeBeads(
        [
            Bead(
                id="b-m",
                labels=["unit:U1", "kind:model"],
                status="open",
                metadata={"unit": "U1", "kind": "model", "author": "claude"},
            ),
            Bead(
                id="b-v",
                labels=["unit:U1", "kind:verify-math"],
                status="open",
                metadata={"unit": "U1", "kind": "verify-math", "author": "codex"},
            ),
            Bead(
                id="b-old",
                labels=["unit:U1", "kind:impl"],
                status="closed",
                metadata={"unit": "U1", "kind": "impl", "author": "codex"},
            ),
        ]
    )
    rows = _run_ok(
        fake=fake, hub=hub, stages="model,verify-math,impl", unit="U1"
    )
    assert [row.bead for row in rows] == ["b-m", "b-v", "fake-1"]
    creates = [entry for entry in fake.argv_log if entry[0] == "create"]
    assert len(creates) == 1
    updates = [entry for entry in fake.argv_log if entry[0] == "update"]
    assert updates == [["update", "b-v", {"parent": "b-m"}]]
    assert fake.show("b-v").metadata["parent"] == "b-m"


class FlakyBeads(FakeBeads):
    """Fails the third create once, then behaves (SPEC §10.2 step 5 rerun)."""

    def __init__(self) -> None:
        super().__init__()
        self._creates = 0
        self._failed = False

    def create(
        self,
        title: str,
        *,
        labels: list[str],
        metadata: dict[str, Any],
        type: str = "task",
        description: str = "",
    ) -> str:
        self._creates += 1
        if self._creates == 3 and not self._failed:
            self._failed = True
            raise RuntimeError("boom on the third create")
        return super().create(
            title,
            labels=labels,
            metadata=metadata,
            type=type,
            description=description,
        )


# Accept 5: crash replay.
def test_crash_replay_creates_deps_and_file_once(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    config = _config(hub)
    fake = FlakyBeads()
    with pytest.raises(RuntimeError, match="boom on the third create"):
        create_unit(
            beads=fake,
            config=config,
            unit="U1",
            title="T",
            stages="frame,survey,model",
            files=None,
            test=None,
        )
    assert not _unit_path(hub).exists()
    assert [e for e in fake.argv_log if e[0] == "create"] != []
    assert [e for e in fake.argv_log if e[0] == "dep"] == []
    rows = create_unit(
        beads=fake,
        config=config,
        unit="U1",
        title="T",
        stages="frame,survey,model",
        files=None,
        test=None,
    )
    creates = [entry for entry in fake.argv_log if entry[0] == "create"]
    assert [entry[1] for entry in creates] == [
        "U1 frame: T",
        "U1 survey: T",
        "U1 model: T",
    ]
    deps = [entry for entry in fake.argv_log if entry[0] == "dep"]
    assert deps == [
        ["dep", "add", "fake-2", "fake-1"],
        ["dep", "add", "fake-3", "fake-2"],
    ]
    assert [row.bead for row in rows] == ["fake-1", "fake-2", "fake-3"]
    assert _unit_path(hub).is_file()
    with pytest.raises(UnitNewError, match="already exists"):
        create_unit(
            beads=fake,
            config=config,
            unit="U1",
            title="T",
            stages="frame,survey,model",
            files=None,
            test=None,
        )


# Accept 6: one-pass substitution.
def test_unit_file_one_pass_substitution(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    _run_ok(
        fake=FakeBeads(),
        hub=hub,
        unit="U9",
        title="fix {unit} and {stages} {title}",
    )
    text = _unit_path(hub, "U9").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "# U9: fix {unit} and {stages} {title}"
    assert lines[3] == "Stages: model,impl"
    assert text.count("{unit}") == 1
    assert text.count("{stages}") == 1
    assert text.count("{title}") == 1


def test_unit_file_matches_template_shape(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    _run_ok(fake=FakeBeads(), hub=hub, unit="U1", title="T")
    text = _unit_path(hub).read_text(encoding="utf-8")
    assert text == (
        "# U1: T\n"
        "\n"
        "Status: open\n"
        "Stages: model,impl\n"
        "\n"
        "## Brief\n"
        "\n"
        "_empty_\n"
        "\n"
        "## Model\n"
        "\n"
        "_empty_\n"
        "\n"
        "## Verify\n"
        "\n"
        "_empty_\n"
        "\n"
        "## Code verify\n"
        "\n"
        "_empty_\n"
        "\n"
        "## Validate\n"
        "\n"
        "_empty_\n"
        "\n"
        "## Report\n"
        "\n"
        "_empty_\n"
    )


# Accept 7: byte-exact table through the real command wiring.
def test_table_output_byte_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = _hub(tmp_path)
    monkeypatch.chdir(hub)
    fake = FakeBeads()
    monkeypatch.setattr(beads_mod, "Beads", lambda cwd: fake)
    assert (
        cli.main(
            [
                "unit",
                "new",
                "U1",
                "T",
                "--stages",
                "model,impl",
                "--files",
                "a.py",
                "--test",
                "run.sh",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert out == (
        "bead\tstage\tauthor\tblocked-by\n"
        "fake-1\tmodel\tclaude\t-\n"
        "fake-2\timpl\tcodex\tfake-1\n"
    )
    assert out == format_table(
        [
            StageRow(bead="fake-1", stage="model", author="claude", blocked_by=None),
            StageRow(
                bead="fake-2", stage="impl", author="codex", blocked_by="fake-1"
            ),
        ]
    )


BD = shutil.which("bd")


# Accept 8: one real bd test in a temp repo.
@pytest.mark.skipif(BD is None, reason="bd is not on PATH")
def test_real_bd_unit_new(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["bd", "init", "--non-interactive"], cwd=tmp_path, check=True, capture_output=True
    )
    from helios.beads import Beads
    from helios.config import load

    config = load(tmp_path)
    assert config.hub == tmp_path
    beads = Beads(tmp_path)
    rows = create_unit(
        beads=beads,
        config=config,
        unit="U7",
        title="Probe",
        stages="model,verify-math,impl",
        files="src/*.py",
        test="uv run pytest -q",
    )
    assert [row.stage for row in rows] == ["model", "verify-math", "impl"]
    assert [row.author for row in rows] == ["claude", "codex", "codex"]
    assert [row.blocked_by for row in rows] == [None, rows[0].bead, rows[1].bead]
    model = beads.show(rows[0].bead)
    assert sorted(model.labels) == ["kind:model", "unit:U7"]
    assert model.title == "U7 model: Probe"
    impl = beads.show(rows[2].bead)
    assert impl.files == ["src/*.py"]
    assert impl.test == "uv run pytest -q"
    verify = beads.show(rows[1].bead)
    assert verify.parent == rows[0].bead
    assert (tmp_path / "docs" / "units" / "U7.md").is_file()


# Round 2: unit id length rule (SPEC §10.2 step 1).
def test_hundred_char_unit_is_accepted(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    unit = "a" * 100
    rows = _run_ok(
        fake=FakeBeads(), hub=hub, unit=unit, stages="model", files=None, test=None
    )
    assert len(rows) == 1
    assert _unit_path(hub, unit).is_file()


# Round 2: unresolvable other is a refusal before any bd write.
def test_unresolvable_other_refuses_without_writes(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    fake = FakeBeads()
    with pytest.raises(
        UnitNewError,
        match=r"^no harness in agents\.verify_order differs from codex$",
    ):
        create_unit(
            beads=fake,
            config=_config(hub, verify_order=("codex",)),
            unit="U1",
            title="T",
            stages="impl,verify-code",
            files="a",
            test="t",
        )
    assert fake.argv_log == []


def test_cli_unresolvable_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = _hub(tmp_path)
    (hub / ".agents").mkdir()
    (hub / ".agents/workflow.toml").write_text('[agents]\nverify_order = ["codex"]\n')
    monkeypatch.chdir(hub)
    fake = FakeBeads()
    monkeypatch.setattr(beads_mod, "Beads", lambda cwd: fake)
    code = cli.main(
        [
            "unit", "new", "U1", "T", "--stages", "impl,verify-code",
            "--files", "a", "--test", "t",
        ]
    )
    err = capsys.readouterr().err
    assert code == 2
    assert err == "helios: no harness in agents.verify_order differs from codex\n"
    assert fake.argv_log == []


# Round 2: creation lock (SPEC §10.2).
def test_held_lock_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = _hub(tmp_path)
    monkeypatch.chdir(hub)
    fake = FakeBeads()
    monkeypatch.setattr(beads_mod, "Beads", lambda cwd: fake)
    lock_path = hub / ".helios" / "runs" / "unit-U9.lock"
    lock_path.parent.mkdir(parents=True)
    with open(lock_path, "a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        args = Namespace(unit="U9", title="T", stages="model", files=None, test=None)
        assert unit_new.run(args) == 2
        err = capsys.readouterr().err
        assert err == "helios: unit U9 is being created by another process\n"
    assert fake.argv_log == []
    assert not _unit_path(hub, "U9").exists()


def test_lock_released_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = _hub(tmp_path)
    monkeypatch.chdir(hub)
    fake = FakeBeads()
    monkeypatch.setattr(beads_mod, "Beads", lambda cwd: fake)
    args = Namespace(unit="U9", title="T", stages="model", files=None, test=None)
    assert unit_new.run(args) == 0
    lock_path = hub / ".helios" / "runs" / "unit-U9.lock"
    assert lock_path.is_file()
    with open(lock_path, "a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# Round 2: ambiguous reuse refuses before any create (SPEC §10.2 step 2).
def test_two_open_beads_refuse(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    fake = FakeBeads(
        [
            Bead(id="open-a", labels=["unit:U1", "kind:impl"], status="in_progress"),
            Bead(id="open-b", labels=["unit:U1", "kind:impl"], status="open"),
        ]
    )
    with pytest.raises(UnitNewError) as excinfo:
        _run_ok(fake=fake, hub=hub, stages="model,impl")
    message = str(excinfo.value)
    assert "impl" in message
    assert "open-a" in message and "open-b" in message
    assert [entry for entry in fake.argv_log if entry[0] == "create"] == []
    assert not _unit_path(hub).exists()


# Round 2: other on a non-verify stage is a refusal (SPEC §5, §10.2 step 3).
def test_other_on_non_verify_stage_refuses(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    fake = FakeBeads()
    with pytest.raises(UnitNewError):
        create_unit(
            beads=fake,
            config=_config(hub, model="other"),
            unit="U1",
            title="T",
            stages="frame,model",
            files=None,
            test=None,
        )
    assert fake.argv_log == []


# Round 2: a reused parent bead's recorded author drives other (SPEC §10.2 step 3).
def test_reused_parent_author_drives_other(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    fake = FakeBeads(
        [
            Bead(
                id="b-impl",
                labels=["unit:U1", "kind:impl"],
                status="open",
                author="opencode",
                metadata={"unit": "U1", "kind": "impl", "author": "opencode"},
            )
        ]
    )
    config = _config(hub, verify_order=("opencode", "codex", "claude"))
    rows = create_unit(
        beads=fake,
        config=config,
        unit="U1",
        title="T",
        stages="impl,verify-code",
        files="a",
        test="t",
    )
    assert [(row.bead, row.author) for row in rows] == [
        ("b-impl", "opencode"),
        ("fake-1", "codex"),
    ]
    assert fake.show("fake-1").metadata["parent"] == "b-impl"
    assert ["dep", "add", "fake-1", "b-impl"] in fake.argv_log


# Round 2: a units directory component that is a file refuses (SPEC §10.2 step 1).
def test_units_dir_component_is_file(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    (hub / "docs").write_text("x", encoding="utf-8")
    fake = FakeBeads()
    with pytest.raises(UnitNewError):
        _run_ok(fake=fake, hub=hub, stages="model", files=None, test=None)
    assert fake.argv_log == []
    assert not (hub / "docs" / "units" / "U1.md").exists()


# Round 3: symlink units components and unit file paths (lstat, lexists).
def _symlink_hub(tmp_path: Path, kind: str) -> tuple[Path, Config]:
    hub = tmp_path / "hub"
    hub.mkdir()
    (tmp_path / "afile").write_text("x", encoding="utf-8")
    (tmp_path / "adir").mkdir()
    notes = hub / "notes"
    if kind == "file":
        notes.write_text("x", encoding="utf-8")
    elif kind == "symlink_file":
        notes.symlink_to(tmp_path / "afile")
    elif kind == "symlink_dir":
        # Inside the hub: a symlink resolving outside the hub is its own
        # refusal, tested by test_units_dir_symlink_outside_hub_refuses.
        (hub / "realdir").mkdir()
        notes.symlink_to(hub / "realdir")
    elif kind == "dangling":
        notes.symlink_to(tmp_path / "missing")
    elif kind == "loop":
        notes.symlink_to(notes)
    elif kind == "unitfile_dangling":
        (hub / "notes" / "units").mkdir(parents=True)
        (hub / "notes" / "units" / "U1.md").symlink_to(tmp_path / "missing.md")
    from helios.config import ProjectConfig

    return hub, Config(hub=hub, project=ProjectConfig(units="notes/units"))


@pytest.mark.parametrize("kind", ["file", "symlink_file", "dangling", "loop"])
def test_units_parent_symlink_refuses(tmp_path: Path, kind: str) -> None:
    hub, config = _symlink_hub(tmp_path, kind)
    fake = FakeBeads()
    with pytest.raises(UnitNewError):
        create_unit(
            beads=fake, config=config, unit="U1", title="T", stages="model",
            files=None, test=None,
        )
    assert fake.argv_log == []


def test_units_parent_valid_symlink_dir_accepted(tmp_path: Path) -> None:
    hub, config = _symlink_hub(tmp_path, "symlink_dir")
    rows = create_unit(
        beads=FakeBeads(), config=config, unit="U1", title="T", stages="model",
        files=None, test=None,
    )
    assert len(rows) == 1
    assert (hub / "realdir" / "units" / "U1.md").is_file()


# Item 2: a units directory component symlinked outside the hub refuses at
# step 1, before the TOCTOU-repeated check in step 5 (SPEC §10.2 step 1,
# round 3 decision).
def test_units_dir_symlink_outside_hub_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / ".git").mkdir()
    (hub / ".agents").mkdir()
    (hub / ".agents" / "workflow.toml").write_text('[project]\nunits = "notes/units"\n')
    outside = tmp_path / "outside"
    outside.mkdir()
    (hub / "notes").symlink_to(outside)
    monkeypatch.chdir(hub)
    fake = FakeBeads()
    monkeypatch.setattr(beads_mod, "Beads", lambda cwd: fake)
    args = Namespace(unit="U1", title="T", stages="model", files=None, test=None)
    assert unit_new.run(args) == 2
    err = capsys.readouterr().err
    assert err == (
        f"helios: units directory resolves outside the hub: {hub / 'notes' / 'units'}\n"
    )
    assert fake.argv_log == []


def test_dangling_unit_file_refuses(tmp_path: Path) -> None:
    hub, config = _symlink_hub(tmp_path, "unitfile_dangling")
    fake = FakeBeads()
    with pytest.raises(UnitNewError, match="already exists"):
        create_unit(
            beads=fake, config=config, unit="U1", title="T", stages="model",
            files=None, test=None,
        )
    assert fake.argv_log == []


# Round 3: the unit id is validated before the lock path is built.
def test_traversal_unit_writes_nothing_anywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = _hub(tmp_path)
    monkeypatch.chdir(hub)
    fake = FakeBeads()
    monkeypatch.setattr(beads_mod, "Beads", lambda cwd: fake)
    code = cli.main(["unit", "new", "../../../../../escape", "T", "--stages", "model"])
    assert code == 2
    assert capsys.readouterr().err.startswith("helios: ")
    assert fake.argv_log == []
    assert not (hub / ".helios").exists()
    assert not (hub / "docs").exists()


# Round 3: an unusable runs directory refuses instead of running unlocked.
def test_runs_dir_unusable_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = _hub(tmp_path)
    (hub / ".agents").mkdir()
    (hub / ".agents" / "workflow.toml").write_text('[project]\nruns = "runs-is-a-file"\n')
    (hub / "runs-is-a-file").write_text("x", encoding="utf-8")
    monkeypatch.chdir(hub)
    fake = FakeBeads()
    monkeypatch.setattr(beads_mod, "Beads", lambda cwd: fake)
    args = Namespace(unit="U8", title="T", stages="model", files=None, test=None)
    assert unit_new.run(args) == 2
    err = capsys.readouterr().err
    assert err.startswith("helios: cannot lock ")
    assert fake.argv_log == []
    assert not _unit_path(hub, "U8").exists()


# Round 3: a reused parent bead without a usable author refuses.
@pytest.mark.parametrize("author", [None, "", "   "])
def test_reused_parent_author_unusable_refuses(
    tmp_path: Path, author: str | None
) -> None:
    hub = _hub(tmp_path)
    meta: dict[str, Any] = {"unit": "U1", "kind": "impl"}
    if author is not None:
        meta["author"] = author
    fake = FakeBeads(
        [
            Bead.from_show(
                {
                    "id": "imp",
                    "labels": ["unit:U1", "kind:impl"],
                    "status": "open",
                    "metadata": meta,
                }
            )
        ]
    )
    with pytest.raises(UnitNewError) as excinfo:
        create_unit(
            beads=fake,
            config=_config(hub),
            unit="U1",
            title="T",
            stages="impl,verify-code",
            files="a",
            test="t",
        )
    assert str(excinfo.value) == "parent bead imp has no usable author"
    assert fake.argv_log == []


# Round 3: a known harness name is usable even when it is not in
# agents.verify_order (round 3 decision item 1).
def test_reused_parent_known_harness_resolves_without_verify_order(
    tmp_path: Path,
) -> None:
    hub = _hub(tmp_path)
    fake = FakeBeads(
        [
            Bead.from_show(
                {
                    "id": "imp",
                    "labels": ["unit:U1", "kind:impl"],
                    "status": "open",
                    "metadata": {"unit": "U1", "kind": "impl", "author": "opencode"},
                }
            )
        ]
    )
    rows = create_unit(
        beads=fake,
        config=_config(hub, verify_order=("opencode", "codex")),
        unit="U1",
        title="T",
        stages="impl,verify-code",
        files="a",
        test="t",
    )
    assert [(row.bead, row.author) for row in rows] == [
        ("imp", "opencode"),
        ("fake-1", "codex"),
    ]


# Round 3: "bogus" no longer resolves (round 2 let it); it fails the item 1
# usable-author regex, so a verify stage resolving `other` against it refuses.
def test_reused_parent_bogus_author_now_refuses(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    fake = FakeBeads(
        [
            Bead.from_show(
                {
                    "id": "imp",
                    "labels": ["unit:U1", "kind:impl"],
                    "status": "open",
                    "metadata": {"unit": "U1", "kind": "impl", "author": "bogus"},
                }
            )
        ]
    )
    with pytest.raises(
        UnitNewError, match=r"^parent bead imp has no usable author$"
    ):
        create_unit(
            beads=fake,
            config=_config(hub, verify_order=("codex",)),
            unit="U1",
            title="T",
            stages="impl,verify-code",
            files="a",
            test="t",
        )
    assert fake.argv_log == []


def test_explicit_profiled_spec_recorded_verbatim(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    fake = FakeBeads()
    rows = create_unit(
        beads=fake,
        config=_config(hub, implement="codex:gpt-5", verify_code="claude:opus"),
        unit="U1",
        title="T",
        stages="impl,verify-code",
        files="a",
        test="t",
    )
    assert [row.author for row in rows] == ["codex:gpt-5", "claude:opus"]
    creates = [entry for entry in fake.argv_log if entry[0] == "create"]
    assert cast(dict[str, Any], creates[0][3])["author"] == "codex:gpt-5"
    assert cast(dict[str, Any], creates[1][3])["author"] == "claude:opus"


# Round 2: file bytes are the one-pass substitution even for a \r title.
# (read_text would normalize \r\n, so compare bytes.)
def test_title_with_carriage_return_byte_exact(tmp_path: Path) -> None:
    from helios.templates import path as template_path

    hub = _hub(tmp_path)
    _run_ok(
        fake=FakeBeads(),
        hub=hub,
        unit="U1",
        title="\r",
        stages="frame",
        files=None,
        test=None,
    )
    raw = (hub / "docs" / "units" / "U1.md").read_bytes()
    template = template_path("unit.md").read_bytes()
    values = {b"unit": b"U1", b"title": b"\r", b"stages": b"frame"}
    assert raw == re.sub(
        rb"\{(unit|title|stages)\}", lambda match: values[match.group(1)], template
    )


# ============================================================
# Round 3 fix 3.
# ============================================================

# Item 1: usable author is `^(claude|codex|opencode|agy)(:[^\s]+)?$`,
# independent of agents.verify_order; only required when a verify stage
# resolves `other` against the parent.
GRID_AUTHORS: list[tuple[Any, bool]] = [
    (None, False),
    ("", False),
    ("   ", False),
    ("bogus", False),
    (" claude", False),
    ("claude ", False),
    (5, False),
    (":opus", False),
    ("opencode", True),
    ("claude", True),
    ("claude:opus", True),
    ("codex", True),
    ("agy:x", True),
]


@pytest.mark.parametrize("verify_spec", ["other", "agy"])
@pytest.mark.parametrize("author,usable", GRID_AUTHORS, ids=[repr(a) for a, _ in GRID_AUTHORS])
def test_usable_author_grid(
    tmp_path: Path, verify_spec: str, author: Any, usable: bool
) -> None:
    hub = _hub(tmp_path)
    meta: dict[str, Any] = {"unit": "U1", "kind": "impl"}
    if author is not None:
        meta["author"] = author
    fake = FakeBeads(
        [
            Bead.from_show(
                {
                    "id": "imp",
                    "labels": ["unit:U1", "kind:impl"],
                    "status": "open",
                    "metadata": meta,
                }
            )
        ]
    )
    config = _config(
        hub, verify_code=verify_spec, verify_order=("claude", "codex", "opencode", "agy")
    )
    kwargs: dict[str, Any] = dict(
        beads=fake,
        config=config,
        unit="U1",
        title="T",
        stages="impl,verify-code",
        files="a",
        test="t",
    )
    if verify_spec == "agy":
        # An explicit spec never needs the parent's author.
        rows = create_unit(**kwargs)
        assert rows[1].author == "agy"
        return
    if not usable:
        with pytest.raises(
            UnitNewError, match=r"^parent bead imp has no usable author$"
        ):
            create_unit(**kwargs)
        assert fake.argv_log == []
        return
    rows = create_unit(**kwargs)
    harness = str(author).split(":", 1)[0]
    expect = next(h for h in config.agents.verify_order if h != harness)
    assert rows[1].author == expect


@pytest.mark.skipif(shutil.which("bd") is None, reason="bd is not on PATH")
@pytest.mark.parametrize("author", ["bogus", ":opus"])
def test_real_bd_unusable_author_refuses(tmp_path: Path, author: str) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["bd", "init", "--non-interactive"], cwd=tmp_path, check=True, capture_output=True
    )
    from helios.beads import Beads
    from helios.config import load

    config = load(tmp_path)
    real_beads = Beads(tmp_path)
    imp_id = real_beads.create(
        "U8 impl: T",
        labels=["unit:U8", "kind:impl"],
        metadata={"unit": "U8", "kind": "impl", "author": author},
        type="task",
        description="",
    )
    with pytest.raises(
        UnitNewError, match=rf"^parent bead {imp_id} has no usable author$"
    ):
        create_unit(
            beads=real_beads,
            config=config,
            unit="U8",
            title="T",
            stages="impl,verify-code",
            files="a",
            test="t",
        )


# Item 2: the units path can change between step 1 and step 5 (TOCTOU); step
# 5 repeats the step 1 path checks (including hub containment) right before
# writing, and any failure there is `cannot write unit file <path>: <message>`
# at exit 1, with the beads already created left in place.
class _SwapOnFirstCreate(FakeBeads):
    """Mutates the filesystem once, on the first `create`, then behaves."""

    def __init__(self, swap: Any) -> None:
        super().__init__()
        self._swap = swap
        self._done = False

    def create(
        self,
        title: str,
        *,
        labels: list[str],
        metadata: dict[str, Any],
        type: str = "task",
        description: str = "",
    ) -> str:
        if not self._done:
            self._done = True
            self._swap()
        return super().create(
            title, labels=labels, metadata=metadata, type=type, description=description
        )


def test_toctou_units_dir_swapped_to_dangling_symlink(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    fake = _SwapOnFirstCreate(
        lambda: (hub / "docs").symlink_to(hub / "missing-target")
    )
    with pytest.raises(UnitWriteError) as excinfo:
        create_unit(
            beads=fake, config=_config(hub), unit="U1", title="T", stages="model",
            files=None, test=None,
        )
    unit_path = _unit_path(hub)
    assert str(excinfo.value) == (
        f"cannot write unit file {unit_path}: "
        f"units directory component is a broken symlink: {hub / 'docs'}"
    )
    assert not unit_path.exists()
    assert len(fake.beads) == 1  # the bead created before the swap stays


def test_toctou_units_dir_swapped_to_file(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    fake = _SwapOnFirstCreate(lambda: (hub / "docs").write_text("x", encoding="utf-8"))
    with pytest.raises(UnitWriteError) as excinfo:
        create_unit(
            beads=fake, config=_config(hub), unit="U1", title="T", stages="model",
            files=None, test=None,
        )
    unit_path = _unit_path(hub)
    assert str(excinfo.value) == (
        f"cannot write unit file {unit_path}: "
        f"units directory component is not a directory: {hub / 'docs'}"
    )
    assert not unit_path.exists()
    assert len(fake.beads) == 1


def test_toctou_units_dir_swapped_outside_hub(tmp_path: Path) -> None:
    # `_hub` makes tmp_path itself the hub, so a sibling of tmp_path is used
    # here instead, to keep "outside" genuinely outside the hub.
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / ".git").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    fake = _SwapOnFirstCreate(lambda: (hub / "docs").symlink_to(outside))
    with pytest.raises(UnitWriteError) as excinfo:
        create_unit(
            beads=fake, config=_config(hub), unit="U1", title="T", stages="model",
            files=None, test=None,
        )
    unit_path = _unit_path(hub)
    assert str(excinfo.value) == (
        f"cannot write unit file {unit_path}: "
        f"units directory resolves outside the hub: {hub / 'docs' / 'units'}"
    )
    assert not unit_path.exists()
    assert not (outside / "units" / "U1.md").exists()
    assert len(fake.beads) == 1


def test_toctou_command_exits_1_not_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = _hub(tmp_path)
    monkeypatch.chdir(hub)
    fake = _SwapOnFirstCreate(lambda: (hub / "docs").write_text("x", encoding="utf-8"))
    monkeypatch.setattr(beads_mod, "Beads", lambda cwd: fake)
    args = Namespace(unit="U1", title="T", stages="model", files=None, test=None)
    assert unit_new.run(args) == 1
    err = capsys.readouterr().err
    assert err == (
        f"helios: cannot write unit file {_unit_path(hub)}: "
        f"units directory component is not a directory: {hub / 'docs'}\n"
    )


# Item 3: the units directory must be writable (checked on its deepest
# existing component), refused before any bd write.
@pytest.mark.skipif(os.geteuid() == 0, reason="running as root")
def test_units_dir_not_writable_refuses(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    docs = hub / "docs"
    docs.mkdir()
    docs.chmod(0o555)
    try:
        fake = FakeBeads()
        with pytest.raises(
            UnitNewError, match=rf"^units directory is not writable: {re.escape(str(docs))}$"
        ):
            _run_ok(fake=fake, hub=hub, stages="model", files=None, test=None)
        assert fake.argv_log == []
    finally:
        docs.chmod(0o755)


# Item 4: the lock file must be a regular file; a FIFO, a symlink (dangling
# or to a real file), or anything else O_NOFOLLOW rejects all refuse quickly
# instead of hanging or accepting a bogus lock.
def _lock_attempt(hub: Path, unit: str, out: list[BaseException | None]) -> None:
    try:
        with unit_lock(hub, ".helios/runs", unit):
            pass
        out.append(None)
    except BaseException as exc:  # noqa: BLE001 - captured across a thread
        out.append(exc)


def test_lock_fifo_refuses_within_two_seconds(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    runs = hub / ".helios" / "runs"
    runs.mkdir(parents=True)
    os.mkfifo(runs / "unit-U1.lock")
    result: list[BaseException | None] = []
    thread = threading.Thread(target=_lock_attempt, args=(hub, "U1", result))
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive(), "lock attempt on a FIFO hung"
    assert isinstance(result[0], UnitNewError)
    assert str(result[0]) == f"cannot lock {runs / 'unit-U1.lock'}: not a regular file"


def test_lock_dangling_symlink_refuses(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    runs = hub / ".helios" / "runs"
    runs.mkdir(parents=True)
    lock = runs / "unit-U1.lock"
    lock.symlink_to(runs / "missing")
    with pytest.raises(
        UnitNewError, match=rf"^cannot lock {re.escape(str(lock))}: not a regular file$"
    ):
        with unit_lock(hub, ".helios/runs", "U1"):
            pass


def test_lock_symlink_to_regular_file_refuses(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    runs = hub / ".helios" / "runs"
    runs.mkdir(parents=True)
    target = runs / "target"
    target.write_text("", encoding="utf-8")
    lock = runs / "unit-U1.lock"
    lock.symlink_to(target)
    with pytest.raises(
        UnitNewError, match=rf"^cannot lock {re.escape(str(lock))}: not a regular file$"
    ):
        with unit_lock(hub, ".helios/runs", "U1"):
            pass


def test_lock_directory_refuses(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    runs = hub / ".helios" / "runs"
    runs.mkdir(parents=True)
    lock = runs / "unit-U1.lock"
    lock.mkdir()
    with pytest.raises(
        UnitNewError, match=rf"^cannot lock {re.escape(str(lock))}: not a regular file$"
    ):
        with unit_lock(hub, ".helios/runs", "U1"):
            pass


# Item 5: a reused bead's row shows its recorded author, or `-` when it has
# none, for any stage kind, not only a verify stage resolving `other`.
def test_reused_bead_with_no_author_shows_dash(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    fake = FakeBeads(
        [
            Bead(
                id="b-frame",
                labels=["unit:U1", "kind:frame"],
                status="open",
                metadata={"unit": "U1", "kind": "frame"},
            )
        ]
    )
    rows = create_unit(
        beads=fake, config=_config(hub), unit="U1", title="T", stages="frame",
        files=None, test=None,
    )
    assert [(row.bead, row.author) for row in rows] == [("b-frame", "-")]


@pytest.mark.parametrize("author", ["", "   "])
def test_reused_bead_with_blank_author_shows_dash(tmp_path: Path, author: str) -> None:
    hub = _hub(tmp_path)
    fake = FakeBeads(
        [
            Bead.from_show(
                {
                    "id": "b-frame",
                    "labels": ["unit:U1", "kind:frame"],
                    "status": "open",
                    "metadata": {"unit": "U1", "kind": "frame", "author": author},
                }
            )
        ]
    )
    rows = create_unit(
        beads=fake, config=_config(hub), unit="U1", title="T", stages="frame",
        files=None, test=None,
    )
    assert [(row.bead, row.author) for row in rows] == [("b-frame", "-")]
