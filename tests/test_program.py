"""Registry and program command tests (SPEC §15.1, §15.3)."""

from __future__ import annotations

import os
import subprocess
import sys
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


def test_show_line_endings_and_sorted_sets() -> None:
    reg = Registry()
    reg.add(
        Block(
            "c",
            "constraint",
            "line1\nline2\r\nline3\rlast",
            satisfies={"R3", "R1", "R2"},
            relaxes={"b", "a"},
        )
    )
    assert prog.show_text(reg) == "## constraint\nc  line1 line2 line3 last  # satisfies R1, R2, R3 / relaxes a, b\n"


def test_show_empty_registry_is_zero_bytes() -> None:
    assert prog.show_text(Registry()) == ""
    only_retired = Registry()
    only_retired.add(Block("x", "set", "e"))
    only_retired.retire("x")
    assert prog.show_text(only_retired) == "## Retired\nx  e\n"


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
    assert prog.check_registry_full(reg, None) == ([], [], [])


def test_check_missing_and_unexpected() -> None:
    reg = Registry()
    reg.add(Block("c1", "constraint", "x", build=recording_build([])))
    reg.add(Block("o1", "objective", "z", build=recording_build(["ghost"])))
    assert prog.check_registry_full(reg, None) == (["c1", "o1"], ["ghost"], [])


def test_check_build_error_line() -> None:
    def bad(model: Any, data: Any) -> None:
        raise RuntimeError("build broke")

    reg = Registry()
    reg.add(Block("ok", "constraint", "x", build=recording_build(["ok"])))
    reg.add(Block("bad", "constraint", "y", build=bad))
    missing, unexpected, errors = prog.check_registry_full(reg, None)
    assert errors == ["error: bad: RuntimeError: build broke"]
    assert "bad" in missing
    assert unexpected == []


@pytest.mark.parametrize(
    "exc",
    ["raise SystemExit(0)", "raise SystemExit('x')", "raise KeyboardInterrupt"],
)
def test_check_build_base_exception(exc: str) -> None:
    reg = Registry()
    namespace: dict[str, Any] = {}
    exec(f"def build(model, data):\n    {exc}", namespace)
    reg.add(Block("c1", "constraint", "x", build=namespace["build"]))
    missing, unexpected, errors = prog.check_registry_full(reg, None)
    assert len(errors) == 1
    assert errors[0].startswith("error: c1: ")
    assert "\n" not in errors[0]
    assert missing == ["c1"]
    assert unexpected == []


def test_recording_model_assignment_and_numbers() -> None:
    seen: list[tuple[str, str]] = []
    kind = ["constraint"]
    model = prog._RecordingModel(seen, kind)
    model.ModelSense = 1
    model.Params["x"] = 1
    model.addConstr(name="c1")
    assert seen == [("constraint", "c1")]
    assert float(model.addVar()) == 0.0
    assert int(model.addVar()) == 0


def test_max_over_recorder_is_build_error() -> None:
    def build(model: Any, data: Any) -> None:
        model.addConstr(max(model.addVars(3)) <= 1, name="c1")

    reg = Registry()
    reg.add(Block("c1", "constraint", "x", build=build))
    missing, unexpected, errors = prog.check_registry_full(reg, None)
    assert len(errors) == 1
    assert errors[0].startswith("error: c1: ValueError: ")


def test_str_satisfies_rejected() -> None:
    with pytest.raises(ValueError):
        Block("x", "set", "e", satisfies="R1")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Block("x", "set", "e", relaxes="R1")  # type: ignore[arg-type]
    reg = Registry()
    block = Block("x", "set", "e")
    block.satisfies = "R1"  # type: ignore[assignment]
    with pytest.raises(ValueError):
        reg.add(block)


def test_recording_model_operators() -> None:
    seen: list[tuple[str, str]] = []
    kind = ["constraint"]
    model = prog._RecordingModel(seen, kind)
    x = model.addVar()
    recorder = model.addConstr(x[0] + 2 * x <= sum([x, x]), name="c[0]")
    assert seen == [("constraint", "c[0]")]
    assert bool(recorder) is True
    assert len(recorder) == 0
    assert list(iter(recorder)) == []
    assert isinstance(-x + +x - x * x / x // x % x**x, prog._Recorder)
    assert isinstance((x << 1 >> 1) & 1 | 1 ^ 1, prog._Recorder)
    assert isinstance(x == 1, prog._Recorder)
    assert isinstance(x != 2, prog._Recorder)
    assert isinstance(1 < x, prog._Recorder)
    assert isinstance(x <= 2, prog._Recorder)
    assert isinstance(abs(~x), prog._Recorder)
    assert isinstance(round(x), prog._Recorder)
    assert isinstance(model[1:2], prog._Recorder)


def test_recording_model_gurobi_style_loop() -> None:
    seen: list[tuple[str, str]] = []
    kind = ["constraint"]
    model = prog._RecordingModel(seen, kind)
    x = model.addVars(3)
    y = model.addVar()
    z = [model.addVar(), model.addVar()]
    for i in range(3):
        model.addConstr(x[i] + 2 * y <= sum(z), name=f"c[{i}]")
    assert seen == [("constraint", f"c[{i}]") for i in range(3)]


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
        "def _boom(model, data):\n"
        "    raise RuntimeError('build broke')\n"
        'REGISTRY.add(Block("c1", "constraint", "x", build=_good))\n'
        'REGISTRY.add(Block("c2", "constraint", "y", build=_bad))\n'
        'REGISTRY.add(Block("c3", "constraint", "z", build=_boom))\n'
    )
    monkeypatch.chdir(hub)
    assert cli.main(["program", "check"]) == 5
    out = capsys.readouterr().out
    assert "error: c3: RuntimeError: build broke\n" in out
    assert "missing: c2, c3\n" in out
    assert "unexpected: ghost\n" in out


def test_import_errors_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / ".agents").mkdir()
    (hub / ".agents" / "workflow.toml").write_text('[project]\nprogram = "nothere"\n')
    monkeypatch.chdir(hub)
    assert cli.main(["program", "show"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("helios: cannot import nothere: ")
    (hub / "nothere.py").write_text("raise RuntimeError('import boom')\n")
    assert cli.main(["program", "show"]) == 2
    err = capsys.readouterr().err
    assert err == "helios: cannot import nothere: RuntimeError: import boom\n"


def git_env() -> dict[str, str]:
    return {
        **dict(os.environ),
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }


def git(path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=path, capture_output=True, text=True, env=git_env()
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def commit_prog(hub: Path, module_file: str, ids: list[str], message: str) -> str:
    source = "from helios.program import Block, Registry\nREGISTRY = Registry()\n" + "".join(
        f"REGISTRY.add(Block('{i}', 'set', 'x'))\n" for i in ids
    )
    target = hub / module_file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)
    git(hub, "add", "-A")
    git(hub, "commit", "-qm", message)
    return git(hub, "rev-parse", "HEAD")


def test_program_diff_basic_and_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    write_hub(hub, "dprog", "")
    git(hub, "init", "-q")
    rev1 = commit_prog(hub, "dprog.py", ["a", "c2", "c10"], "r1")
    rev2 = commit_prog(hub, "dprog.py", ["a", "c3", "b10", "b9"], "r2")
    monkeypatch.chdir(hub)
    assert cli.main(["program", "diff", rev1, rev2]) == 0
    assert capsys.readouterr().out == "+b10\n+b9\n+c3\n-c10\n-c2\n"
    assert cli.main(["program", "diff", rev1, rev1]) == 0
    assert capsys.readouterr().out == ""
    assert cli.main(["program", "diff", rev1, "deadbeef"]) == 2
    assert "helios: " in capsys.readouterr().err


def test_program_diff_src_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / ".agents").mkdir()
    (hub / ".agents" / "workflow.toml").write_text('[project]\nprogram = "dpkg.mod"\n')
    git(hub, "init", "-q")
    rev1 = commit_prog(hub, "src/dpkg/mod.py", ["a"], "r1")
    rev2 = commit_prog(hub, "src/dpkg/mod.py", ["a", "b"], "r2")
    commit_prog(hub, "dpkg/mod.py", ["rootonly"], "r3")
    monkeypatch.chdir(hub)
    assert cli.main(["program", "diff", rev1, rev2]) == 0
    assert capsys.readouterr().out == "+b\n"


def test_program_diff_dataclass_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    write_hub(hub, "dcprog", "")
    git(hub, "init", "-q")
    src = (
        "import dataclasses\nfrom helios.program import Block, Registry\n"
        "@dataclasses.dataclass\nclass D:\n    n: int = 1\n"
        "REGISTRY = Registry()\nREGISTRY.add(Block('a', 'set', 'x'))\n"
    )
    (hub / "dcprog.py").write_text(src)
    git(hub, "add", "-A")
    git(hub, "commit", "-qm", "r1")
    rev1 = git(hub, "rev-parse", "HEAD")
    (hub / "dcprog.py").write_text(src + "REGISTRY.add(Block('b', 'set', 'y'))\n")
    git(hub, "add", "-A")
    git(hub, "commit", "-qm", "r2")
    rev2 = git(hub, "rev-parse", "HEAD")
    monkeypatch.chdir(hub)
    assert cli.main(["program", "diff", rev1, rev2]) == 0
    assert capsys.readouterr().out == "+b\n"


def test_program_diff_sibling_import_resolves_at_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    write_hub(hub, "sibprog", "")
    git(hub, "init", "-q")
    (hub / "sibhelp.py").write_text("IDS = ['old1']\n")
    (hub / "sibprog.py").write_text(
        "from helios.program import Block, Registry\nfrom sibhelp import IDS\n"
        "REGISTRY = Registry()\nfor i in IDS:\n    REGISTRY.add(Block(i, 'set', 'x'))\n"
    )
    git(hub, "add", "-A")
    git(hub, "commit", "-qm", "r1")
    rev1 = git(hub, "rev-parse", "HEAD")
    (hub / "sibhelp.py").write_text("IDS = ['new1']\n")
    git(hub, "add", "-A")
    git(hub, "commit", "-qm", "r2")
    rev2 = git(hub, "rev-parse", "HEAD")
    monkeypatch.chdir(hub)
    assert cli.main(["program", "diff", rev1, rev2]) == 0
    assert capsys.readouterr().out == "+new1\n-old1\n"


def test_program_diff_syntax_error_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    write_hub(hub, "seprog", "")
    git(hub, "init", "-q")
    rev1 = commit_prog(hub, "seprog.py", ["a"], "r1")
    (hub / "seprog.py").write_text("def broken(:\n")
    git(hub, "add", "-A")
    git(hub, "commit", "-qm", "r2")
    rev2 = git(hub, "rev-parse", "HEAD")
    monkeypatch.chdir(hub)
    assert cli.main(["program", "diff", rev1, rev2]) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"helios: cannot import seprog at {rev2}: SyntaxError: ")


def test_program_diff_removed_file_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    write_hub(hub, "rmprog", "")
    git(hub, "init", "-q")
    rev1 = commit_prog(hub, "rmprog.py", ["a"], "r1")
    (hub / "rmprog.py").unlink()
    git(hub, "add", "-A")
    git(hub, "commit", "-qm", "r2")
    rev2 = git(hub, "rev-parse", "HEAD")
    monkeypatch.chdir(hub)
    assert cli.main(["program", "diff", rev1, rev2]) == 2


def test_program_diff_ignores_import_prints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    write_hub(hub, "prprog", "")
    git(hub, "init", "-q")
    (hub / "prprog.py").write_text(
        "print('loading data')\n"
        "from helios.program import Block, Registry\n"
        "REGISTRY = Registry()\nREGISTRY.add(Block('a', 'set', 'x'))\n"
    )
    git(hub, "add", "-A")
    git(hub, "commit", "-qm", "r1")
    rev1 = git(hub, "rev-parse", "HEAD")
    (hub / "prprog.py").write_text(
        "print('loading data')\n"
        "from helios.program import Block, Registry\n"
        "REGISTRY = Registry()\nREGISTRY.add(Block('a', 'set', 'x'))\n"
        "REGISTRY.add(Block('b', 'set', 'y'))\n"
    )
    git(hub, "add", "-A")
    git(hub, "commit", "-qm", "r2")
    rev2 = git(hub, "rev-parse", "HEAD")
    monkeypatch.chdir(hub)
    assert cli.main(["program", "diff", rev1, rev2]) == 0
    assert capsys.readouterr().out == "+b\n"


def test_program_diff_no_cwd_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    write_hub(hub, "lkprog", "")
    git(hub, "init", "-q")
    (hub / "lkprog.py").write_text(
        "from helios.program import Block, Registry\nfrom lkhelp import IDS\n"
        "REGISTRY = Registry()\nfor i in IDS:\n    REGISTRY.add(Block(i, 'set', 'x'))\n"
    )
    git(hub, "add", "-A")
    git(hub, "commit", "-qm", "r1")
    rev1 = git(hub, "rev-parse", "HEAD")
    (hub / "lkhelp.py").write_text("IDS = ['h']\n")
    git(hub, "add", "-A")
    git(hub, "commit", "-qm", "r2")
    rev2 = git(hub, "rev-parse", "HEAD")
    (hub / "lkhelp.py").write_text("IDS = ['h', 'dirty']\n")
    (hub / "sub").mkdir()
    for cwd in (hub, hub / "sub"):
        monkeypatch.chdir(cwd)
        assert cli.main(["program", "diff", rev1, rev2]) == 2
        err = capsys.readouterr().err
        assert err.startswith(
            f"helios: cannot import lkprog at {rev1}: ModuleNotFoundError: "
        ), (cwd, err)


@pytest.mark.parametrize(
    "src", ["raise SystemExit(0)\n", "raise SystemExit('x')\n", "raise KeyboardInterrupt\n"]
)
def test_program_show_check_import_base_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, src: str
) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / ".agents").mkdir()
    (hub / ".agents" / "workflow.toml").write_text('[project]\nprogram = "syprog"\n')
    (hub / "syprog.py").write_text(src)
    monkeypatch.chdir(hub)
    assert cli.main(["program", "show"]) == 2
    assert capsys.readouterr().err.startswith("helios: cannot import syprog: ")
    assert cli.main(["program", "check"]) == 2
    assert capsys.readouterr().err.startswith("helios: cannot import syprog: ")


def test_two_hubs_same_module(tmp_path: Path) -> None:
    res = {}
    for layout in ("flat", "dotted"):
        ids = []
        for i in (1, 2):
            hub = tmp_path / f"{layout}{i}"
            if layout == "flat":
                hub.mkdir()
                (hub / "sameprog2.py").write_text(
                    "from helios.program import Block, Registry\nREGISTRY = Registry()\n"
                    f"REGISTRY.add(Block('h{i}', 'set', 'x'))\n"
                )
                name = "sameprog2"
            else:
                (hub / "src" / "samepkg2").mkdir(parents=True)
                (hub / "src" / "samepkg2" / "__init__.py").write_text("")
                (hub / "src" / "samepkg2" / "prog.py").write_text(
                    "from helios.program import Block, Registry\nREGISTRY = Registry()\n"
                    f"REGISTRY.add(Block('h{i}', 'set', 'x'))\n"
                )
                name = "samepkg2.prog"
            _, reg = prog.load_registry(hub, name)
            ids.append([b.id for b in reg.active()])
        res[layout] = ids
    assert res["flat"] == [["h1"], ["h2"]]
    assert res["dotted"] == [["h1"], ["h2"]]


def test_real_cli_diff_subprocess(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    write_hub(hub, "cliprog", "")
    git(hub, "init", "-q")
    rev1 = commit_prog(hub, "cliprog.py", ["a"], "r1")
    rev2 = commit_prog(hub, "cliprog.py", ["a", "b"], "r2")
    proc = subprocess.run(
        [sys.executable, "-m", "helios.cli", "program", "diff", rev1, rev2],
        cwd=hub,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "+b\n"


# ============================================================ round 3 review fixes


@pytest.mark.parametrize("where", ["root", "src"])
def test_program_diff_child_stdlib_named_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, where: str
) -> None:
    """A committed json.py must not shadow the diff child's own json import (decided)."""
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / ".agents").mkdir(parents=True)
    (hub / ".agents" / "workflow.toml").write_text('[project]\nprogram = "jprog"\n')
    git(hub, "init", "-q")
    pre = "" if where == "root" else "src/"
    reg_src = (
        "from helios.program import Block, Registry\nREGISTRY = Registry()\n"
        "REGISTRY.add(Block('a', 'set', 'x'))\n"
    )
    for rel in (f"{pre}jprog.py", f"{pre}json.py"):
        (hub / rel).parent.mkdir(parents=True, exist_ok=True)
    (hub / f"{pre}jprog.py").write_text(reg_src)
    (hub / f"{pre}json.py").write_text("DATA = {'k': 1}\n")
    git(hub, "add", "-A")
    git(hub, "commit", "-qm", "r1")
    rev1 = git(hub, "rev-parse", "HEAD")
    (hub / f"{pre}jprog.py").write_text(reg_src + "REGISTRY.add(Block('b', 'set', 'x'))\n")
    git(hub, "add", "-A")
    git(hub, "commit", "-qm", "r2")
    rev2 = git(hub, "rev-parse", "HEAD")
    monkeypatch.chdir(hub)
    assert cli.main(["program", "diff", rev1, rev2]) == 0
    assert capsys.readouterr().out == "+b\n"


def test_program_diff_child_imports_third_party(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A registry module importing sympy still resolves in the diff child (SPEC §15.1, decided)."""
    hub = tmp_path / "hub"
    hub.mkdir()
    write_hub(hub, "syprog", "")
    git(hub, "init", "-q")
    src = (
        "import sympy\nfrom helios.program import Block, Registry\n"
        "x = sympy.Symbol('x')\nREGISTRY = Registry()\n"
        "REGISTRY.add(Block('c1', 'constraint', x <= 1))\n"
    )
    (hub / "syprog.py").write_text(src)
    git(hub, "add", "-A")
    git(hub, "commit", "-qm", "r1")
    rev1 = git(hub, "rev-parse", "HEAD")
    (hub / "syprog.py").write_text(src + "REGISTRY.add(Block('c2', 'constraint', 2 * x <= 3))\n")
    git(hub, "add", "-A")
    git(hub, "commit", "-qm", "r2")
    rev2 = git(hub, "rev-parse", "HEAD")
    monkeypatch.chdir(hub)
    assert cli.main(["program", "diff", rev1, rev2]) == 0
    assert capsys.readouterr().out == "+c2\n"


def test_program_diff_tempdir_left_empty(tmp_path: Path) -> None:
    """The extracted tree and the ids file both sit under one TemporaryDirectory (decided).

    Run as a real subprocess (like test_real_cli_diff_subprocess): tempfile
    caches TMPDIR per process, so an in-process cli.main call would not see a
    monkeypatched TMPDIR reliably.
    """
    hub = tmp_path / "hub"
    hub.mkdir()
    write_hub(hub, "tfprog", "")
    git(hub, "init", "-q")
    rev1 = commit_prog(hub, "tfprog.py", ["a"], "r1")
    rev2 = commit_prog(hub, "tfprog.py", ["b"], "r2")
    tmpd = tmp_path / "tmpd"
    tmpd.mkdir()
    env = {**os.environ, "TMPDIR": str(tmpd)}

    def run_diff(*rev: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "helios.cli", "program", "diff", *rev],
            cwd=hub,
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

    proc = run_diff(rev1, rev2)
    assert (proc.returncode, proc.stdout) == (0, "+b\n-a\n"), proc.stderr
    assert list(tmpd.iterdir()) == []
    proc2 = run_diff(rev1, "deadbeef")
    assert proc2.returncode == 2
    assert list(tmpd.iterdir()) == []
