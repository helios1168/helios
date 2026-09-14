"""Tests for `helios unit new` (SPEC §10.2)."""

from __future__ import annotations

import shutil
import subprocess
from argparse import Namespace
from pathlib import Path
from typing import Any, cast

import pytest

from helios import beads as beads_mod
from helios import cli
from helios.beads import Bead, FakeBeads
from helios.commands import unit_new
from helios.config import AgentsConfig, Config
from helios.units import StageRow, UnitNewError, create_unit, format_table


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
    {"unit": "U1", "stages": "model,validate", "files": "a.py", "test": None},
    {"unit": "U1", "stages": "impl", "files": "a,,b", "test": "t"},
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
    assert capsys.readouterr().err.strip() != ""
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
