"""Registry and program command tests (SPEC §15.1, §15.3)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from helios import cli, program as prog
from helios.program import Block, Registry


def test_block_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        Block("x", "nope", "expr")


def test_registry_add_retire_active_retired() -> None:
    reg = Registry()
    reg.add(Block("b", "constraint", "x"))
    reg.add(Block("a", "objective", "y"))
    assert [b.id for b in reg.active()] == ["b", "a"]
    assert reg.retired() == []
    reg.retire("b")
    assert [b.id for b in reg.active()] == ["a"]
    assert [b.id for b in reg.retired()] == ["b"]


def test_registry_never_reuses_ids() -> None:
    reg = Registry()
    reg.add(Block("b", "constraint", "x"))
    with pytest.raises(ValueError, match="b"):
        reg.add(Block("b", "objective", "y"))
    reg.retire("b")
    with pytest.raises(ValueError, match="b"):
        reg.add(Block("b", "set", "z"))


def test_registry_retire_unknown_raises_key_error() -> None:
    reg = Registry()
    with pytest.raises(KeyError):
        reg.retire("missing")
    reg.add(Block("b", "constraint", "x"))
    reg.retire("b")
    with pytest.raises(KeyError):
        reg.retire("b")


def test_registry_active_honors_omit(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = Registry()
    reg.add(Block("a", "constraint", "x"))
    reg.add(Block("b", "constraint", "y"))
    reg.add(Block("c", "constraint", "z"))
    monkeypatch.setenv("HELIOS_OMIT", "a, c,, ")
    assert [b.id for b in reg.active()] == ["b"]
    assert [b.id for b in reg.retired()] == []
    reg.retire("b")
    assert [b.id for b in reg.retired()] == ["b"]


def full_registry() -> Registry:
    reg = Registry()
    reg.add(Block("v1", "variable", "x"))
    reg.add(Block("p1", "parameter", "n = 3"))
    reg.add(Block("s1", "set", "I"))
    reg.add(Block("d1", "definition", "y = x + 1", satisfies=("R1",)))
    reg.add(Block("c1", "constraint", "x <= 1", satisfies=("R1", "R2"), relaxes=("r1",)))
    reg.add(Block("m1", "constraint", "a\nb"))
    reg.add(Block("o1", "objective", "min x"))
    reg.add(Block("g1", "stage", "solve"))
    reg.add(Block("z9", "constraint", "old", satisfies=("R5",)))
    reg.retire("z9")
    return reg


EXPECTED_SHOW = """\
## set
s1  I
## parameter
p1  n = 3
## variable
v1  x
## definition
d1  y = x + 1  # satisfies R1
## objective
o1  min x
## constraint
c1  x <= 1  # satisfies R1, R2 / relaxes r1
m1  a b
## stage
g1  solve
## Retired
z9  old  # satisfies R5
"""


def test_show_byte_exact() -> None:
    assert prog.show_text(full_registry()) == EXPECTED_SHOW


def test_show_same_registry_same_bytes() -> None:
    assert prog.show_text(full_registry()) == prog.show_text(full_registry())
    other = Registry()
    for block in reversed(full_registry().active()):
        other.add(Block(block.id, block.kind, block.expr))
    assert prog.show_text(other) == (
        "## set\ns1  I\n## parameter\np1  n = 3\n## variable\nv1  x\n"
        "## definition\nd1  y = x + 1\n## objective\no1  min x\n"
        "## constraint\nc1  x <= 1\nm1  a b\n## stage\ng1  solve\n"
    )


def recording_build(names: list[str]) -> Callable[[Any, Any], None]:
    def build(model: Any, data: Any) -> None:
        for name in names:
            getattr(model, "add")(name=name)

    return build


def test_check_ok() -> None:
    reg = Registry()
    reg.add(Block("c1", "constraint", "x", build=recording_build(["c1"])))
    reg.add(Block("c2", "constraint", "y", build=recording_build(["c2[0]", "c2[1]"])))
    reg.add(Block("o1", "objective", "z", build=recording_build(["o1"])))
    reg.add(Block("v1", "variable", "w", build=recording_build(["junk"])))
    reg.add(Block("d1", "definition", "q"))
    assert prog.check_registry(reg, None) == ([], [])


def test_check_missing_and_unexpected() -> None:
    reg = Registry()
    reg.add(Block("c1", "constraint", "x", build=recording_build([])))
    reg.add(Block("o1", "objective", "z", build=recording_build(["ghost"])))
    assert prog.check_registry(reg, None) == (["c1", "o1"], ["ghost"])


def write_hub(hub: Path, module_name: str, source: str) -> None:
    (hub / ".agents").mkdir(parents=True, exist_ok=True)
    (hub / ".agents" / "workflow.toml").write_text(f'[project]\nprogram = "{module_name}"\n')
    (hub / f"{module_name}.py").write_text(source)


def test_program_show_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / ".agents").mkdir()
    (hub / ".agents" / "workflow.toml").write_text('[project]\nprogram = "showprog"\n')
    (hub / "showprog.py").write_text(
        "from helios.program import Block, Registry\n"
        "REGISTRY = Registry()\n"
        'REGISTRY.add(Block("c1", "constraint", "x <= 1", satisfies=("R1",)))\n'
    )
    monkeypatch.chdir(hub)
    assert cli.main(["program", "show"]) == 0
    assert capsys.readouterr().out == "## constraint\nc1  x <= 1  # satisfies R1\n"


def test_program_show_output_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / ".agents").mkdir()
    (hub / ".agents" / "workflow.toml").write_text('[project]\nprogram = "showprog2"\n')
    (hub / "showprog2.py").write_text(
        "from helios.program import Block, Registry\n"
        "REGISTRY = Registry()\n"
        'REGISTRY.add(Block("c1", "constraint", "x"))\n'
    )
    monkeypatch.chdir(hub)
    assert cli.main(["program", "show", "--output", "out.md"]) == 0
    assert (hub / "out.md").read_text() == "## constraint\nc1  x\n"
    assert capsys.readouterr().out == ""


def test_program_check_command_exit_0_and_5(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / ".agents").mkdir()
    (hub / ".agents" / "workflow.toml").write_text('[project]\nprogram = "chkprog"\n')
    (hub / "chkprog.py").write_text(
        "from helios.program import Block, Registry\n"
        "REGISTRY = Registry()\n"
        "def _good(model, data):\n"
        '    model.add(name="c1")\n'
        "def _bad(model, data):\n"
        '    model.add(name="ghost")\n'
        'REGISTRY.add(Block("c1", "constraint", "x", build=_good))\n'
        'REGISTRY.add(Block("c2", "constraint", "y", build=_bad))\n'
    )
    monkeypatch.chdir(hub)
    assert cli.main(["program", "check"]) == 5
    out = capsys.readouterr().out
    assert "missing: c2\n" in out
    assert "unexpected: ghost\n" in out


def git(path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=path,
        capture_output=True,
        text=True,
        env={
            **dict(os.environ),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def make_repo(hub: Path, layout: str) -> tuple[str, str]:
    write_hub(
        hub,
        "dpkg.mod" if layout == "src" else "dprog",
        "from helios.program import Block, Registry\n"
        "REGISTRY = Registry()\n"
        'REGISTRY.add(Block("a", "constraint", "x"))\n'
        'REGISTRY.add(Block("b", "constraint", "y"))\n',
    )
    if layout == "src":
        (hub / "src" / "dpkg").mkdir(parents=True)
        (hub / "src" / "dpkg" / "mod.py").write_text((hub / "dpkg.mod.py").read_text())
        (hub / "dpkg.mod.py").unlink()
        (hub / ".agents" / "workflow.toml").write_text('[project]\nprogram = "dpkg.mod"\n')
        target = "src/dpkg/mod.py"
    else:
        target = "dprog.py"
    git(hub, "init", "-q")
    git(hub, "add", ".")
    git(hub, "commit", "-qm", "v1")
    rev1 = git(hub, "rev-parse", "HEAD")
    old = (hub / target).read_text()
    (hub / target).write_text(old.replace('Block("b"', 'Block("c"'))
    git(hub, "add", ".")
    git(hub, "commit", "-qm", "v2")
    rev2 = git(hub, "rev-parse", "HEAD")
    return rev1, rev2


@pytest.mark.parametrize("layout", ["root", "src"])
def test_program_diff_layouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, layout: str
) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    rev1, rev2 = make_repo(hub, layout)
    monkeypatch.chdir(hub)
    assert cli.main(["program", "diff", rev1, rev2]) == 0
    assert capsys.readouterr().out == "+c\n-b\n"


def test_program_diff_absent_revision_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / ".agents").mkdir()
    (hub / ".agents" / "workflow.toml").write_text('[project]\nprogram = "nope.mod"\n')
    git(hub, "init", "-q")
    git(hub, "commit", "-q", "--allow-empty", "-m", "empty")
    rev = git(hub, "rev-parse", "HEAD")
    monkeypatch.chdir(hub)
    assert cli.main(["program", "diff", rev, rev]) == 2
    assert "not found" in capsys.readouterr().err
