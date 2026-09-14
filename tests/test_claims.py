"""Claims check and attack tests (SPEC §15.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helios import claims as claims_lib
from helios import cli

BACKENDS = """\
[backend.prover]
methods = ["proof", "counterexample"]
scopes = ["universal", "instance"]
[backend.meter]
methods = ["numerical_certificate"]
scopes = ["instance"]
[backend.manual]
methods = ["proof", "review"]
scopes = ["universal", "bounded", "instance"]
"""


def make_hub(
    tmp_path: Path,
    tag: str,
    prog_src: str,
    claims_src: str,
    backends: str | None = BACKENDS,
) -> Path:
    hub = tmp_path / f"hub-{tag}"
    hub.mkdir(parents=True)
    (hub / ".agents").mkdir()
    (hub / ".agents" / "workflow.toml").write_text(
        f'[project]\nprogram = "prog_{tag}"\nclaims = "clm_{tag}"\n'
    )
    if backends is not None:
        (hub / ".agents" / "backends.toml").write_text(backends)
    (hub / f"prog_{tag}.py").write_text(prog_src)
    (hub / f"clm_{tag}.py").write_text(claims_src)
    return hub


BASE_PROG = """\
from helios.program import Block, Registry
REGISTRY = Registry()
REGISTRY.add(Block("b1", "constraint", "x <= 1"))
REGISTRY.add(Block("b2", "objective", "min x"))
"""


def test_claim_decorator_registers() -> None:
    before = len(claims_lib._CLAIMS)

    @claims_lib.claim(
        "reg-probe", covers=("b1",), backend="prover", method="proof", scope="universal"
    )
    def probe() -> bool:
        return True

    assert claims_lib._CLAIMS["reg-probe"].func is probe
    assert claims_lib._CLAIMS["reg-probe"].covers == ("b1",)
    assert len(claims_lib._CLAIMS) == before + 1


def test_backends_fallback_to_templates(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    hub.mkdir()
    backends = claims_lib.load_backends(hub)
    assert "sympy" in backends
    assert set(backends["sympy"].methods) >= {"proof", "counterexample"}


def test_backends_project_file_wins(tmp_path: Path) -> None:
    hub = make_hub(tmp_path, "bk", BASE_PROG, "")
    backends = claims_lib.load_backends(hub)
    assert set(backends) == {"prover", "meter", "manual"}


def test_validate_problems() -> None:
    backends = {
        "prover": claims_lib.Backend(["proof", "counterexample"], ["universal"]),
    }

    def mk(name: str, **kw) -> claims_lib.Claim:
        base = dict(
            name=name, covers=("b1",), backend="prover", method="proof", scope="universal"
        )
        base.update(kw)
        return claims_lib.Claim(**base)  # type: ignore[arg-type]

    assert claims_lib.validate_claims([mk("ok")], {"b1"}, backends) == []
    problems = claims_lib.validate_claims(
        [
            mk("u", covers=("nope",)),
            mk("r", covers=("R1",)),
            mk("b", backend="ghost"),
            mk("m", method="review"),
            mk("s", scope="bounded"),
        ],
        {"b1"},
        backends,
    )
    assert "u: unknown block id 'nope'" in problems
    assert "r: covers no active block" in problems
    assert "b: unknown backend 'ghost'" in problems
    assert "m: backend 'prover' does not allow method 'review'" in problems
    assert "s: backend 'prover' does not allow scope 'bounded'" in problems


def run_check(
    hub: Path, monkeypatch: pytest.MonkeyPatch, capsys, *argv: str
) -> tuple[int, list[str], str]:
    monkeypatch.chdir(hub)
    code = cli.main(["claims", "check", *argv])
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err


def test_check_verified_exit_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = make_hub(
        tmp_path,
        "ok",
        BASE_PROG,
        'from helios.claims import claim\n'
        '@claim("c_ok", covers=("b1", "R3"), backend="prover",'
        ' method="proof", scope="universal")\n'
        "def c_ok():\n"
        "    return True\n",
    )
    code, lines, err = run_check(hub, monkeypatch, capsys)
    assert code == 0
    assert err == ""
    assert len(lines) == 1
    finding = json.loads(lines[0])
    assert finding["id"] == "c_ok"
    assert finding["claim"] == "c_ok"
    assert finding["verdict"] == "verified"
    assert finding["method"] == "proof"
    assert finding["scope"] == "universal"
    assert finding["covers"] == ["b1", "R3"]


def test_check_refuted_exit_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = make_hub(
        tmp_path,
        "ref",
        BASE_PROG,
        'from helios.claims import claim\n'
        '@claim("c_no", covers=("b1",), backend="prover",'
        ' method="proof", scope="universal")\n'
        "def c_no():\n"
        "    return False\n",
    )
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    assert code == 3
    finding = json.loads(lines[0])
    assert finding["verdict"] == "refuted"
    assert finding["method"] == "counterexample"


def test_check_false_without_counterexample_is_inconclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = make_hub(
        tmp_path,
        "inc",
        BASE_PROG,
        'from helios.claims import claim\n'
        '@claim("c_m", covers=("b1",), backend="meter",'
        ' method="numerical_certificate", scope="instance",'
        ' bound={"instance": "i1"})\n'
        "def c_m():\n"
        "    return False\n",
    )
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    assert code == 3
    finding = json.loads(lines[0])
    assert finding["verdict"] == "inconclusive"
    assert finding["bound"] == {"instance": "i1"}


def test_check_non_bool_return_is_inconclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = make_hub(
        tmp_path,
        "nb",
        BASE_PROG,
        'from helios.claims import claim\n'
        '@claim("c_nb", covers=("b1",), backend="prover",'
        ' method="proof", scope="universal")\n'
        "def c_nb():\n"
        "    return 42\n",
    )
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    assert code == 3
    finding = json.loads(lines[0])
    assert finding["verdict"] == "inconclusive"
    assert "42" in finding["notes"]
    assert not (hub / ".helios" / "claims" / "c_nb.traceback.txt").exists()


def test_check_error_saves_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = make_hub(
        tmp_path,
        "er",
        BASE_PROG,
        'from helios.claims import claim\n'
        '@claim("c_er", covers=("b1",), backend="prover",'
        ' method="proof", scope="universal")\n'
        "def c_er():\n"
        "    raise RuntimeError('boom')\n",
    )
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    assert code == 3
    finding = json.loads(lines[0])
    assert finding["verdict"] == "inconclusive"
    assert finding["artifact"] == ".helios/claims/c_er.traceback.txt"
    assert "boom" in (hub / ".helios" / "claims" / "c_er.traceback.txt").read_text()


def test_check_timeout_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    hub = make_hub(
        tmp_path,
        "to",
        BASE_PROG,
        "import time\n"
        "from helios.claims import claim\n"
        '@claim("c_to", covers=("b1",), backend="prover",'
        ' method="proof", scope="universal")\n'
        "def c_to():\n"
        "    time.sleep(30)\n"
        "    return True\n",
    )
    code, lines, _ = run_check(hub, monkeypatch, capsys, "--timeout", "1")
    assert code == 3
    finding = json.loads(lines[0])
    assert finding["verdict"] == "inconclusive"
    assert finding["notes"] == "timeout after 1 s"


def test_check_finding_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = make_hub(
        tmp_path,
        "fp",
        BASE_PROG,
        "from helios.claims import claim\n"
        "from helios.envelope import Finding\n"
        '@claim("c_fp", covers=("b1",), backend="prover",'
        ' method="proof", scope="universal")\n'
        "def c_fp():\n"
        '    return Finding(id="c_fp", claim="c_fp", covers=["b1"],'
        ' verdict="verified", method="proof", scope="universal")\n',
    )
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    assert code == 0
    assert json.loads(lines[0])["verdict"] == "verified"


def test_check_manual_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = make_hub(
        tmp_path,
        "man",
        BASE_PROG,
        'from helios.claims import claim\n'
        '@claim("c_m", covers=("b1",), backend="manual",'
        ' method="review", scope="universal")\n'
        "def c_m():\n"
        "    return None\n"
        '@claim("c_ok", covers=("b1",), backend="prover",'
        ' method="proof", scope="universal")\n'
        "def c_ok():\n"
        "    return True\n",
    )
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    assert code == 0
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == "c_ok"


def test_check_validation_fails_before_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    prog_src = (
        "from helios.program import Block, Registry\n"
        "REGISTRY = Registry()\n"
        'REGISTRY.add(Block("b1", "constraint", "x"))\n'
        'REGISTRY.add(Block("old", "constraint", "y"))\n'
        'REGISTRY.retire("old")\n'
    )
    hub = make_hub(
        tmp_path,
        "pre",
        prog_src,
        'from helios.claims import claim\n'
        '@claim("c_u", covers=("nope",), backend="prover",'
        ' method="proof", scope="universal")\n'
        "def c_u():\n"
        "    raise RuntimeError('must not run')\n"
        '@claim("c_r", covers=("old", "R2"), backend="prover",'
        ' method="proof", scope="universal")\n'
        "def c_r():\n"
        "    return True\n"
        '@claim("c_q", covers=("R1",), backend="prover",'
        ' method="proof", scope="universal")\n'
        "def c_q():\n"
        "    return True\n"
        '@claim("c_b", covers=("b1",), backend="ghost",'
        ' method="proof", scope="universal")\n'
        "def c_b():\n"
        "    return True\n",
    )
    code, lines, err = run_check(hub, monkeypatch, capsys)
    assert code == 2
    assert lines == []
    assert "c_u: unknown block id 'nope'" in err
    assert "c_r: unknown block id 'old'" in err
    assert "c_q: covers no active block" in err
    assert "c_b: unknown backend 'ghost'" in err
    assert not (hub / ".helios").exists()


def test_check_filters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    src = (
        'from helios.claims import claim\n'
        '@claim("c_a", covers=("b1",), backend="prover",'
        ' method="proof", scope="universal")\n'
        "def c_a():\n"
        "    return True\n"
        '@claim("c_b", covers=("b2",), backend="meter",'
        ' method="numerical_certificate", scope="instance",'
        ' bound={"instance": "i1"})\n'
        "def c_b():\n"
        "    return True\n"
    )
    hub = make_hub(tmp_path, "flt", BASE_PROG, src)
    code, lines, _ = run_check(hub, monkeypatch, capsys, "--backend", "meter")
    assert code == 0
    assert [json.loads(l)["id"] for l in lines] == ["c_b"]
    code, lines, _ = run_check(hub, monkeypatch, capsys, "--covers", "b1")
    assert code == 0
    assert [json.loads(l)["id"] for l in lines] == ["c_a"]
    code, lines, _ = run_check(hub, monkeypatch, capsys, "--covers", "nothing")
    assert code == 0
    assert lines == []


def run_attack(
    hub: Path, monkeypatch: pytest.MonkeyPatch, capsys, *argv: str
) -> tuple[int, list[str], str]:
    monkeypatch.chdir(hub)
    code = cli.main(["claims", "attack", *argv])
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err


def attack_hub(
    tmp_path: Path,
    body: str,
    tag: str,
    covers: str = '("b1", "R7", "b2")',
    redundant: str = '("b2",)',
) -> Path:
    src = (
        "from helios.claims import claim\n"
        f'@claim("c_atk", covers={covers}, backend="prover",'
        f' method="proof", scope="universal", redundant={redundant})\n'
        "def c_atk():\n"
        f"{body}\n"
    )
    return make_hub(tmp_path, tag, BASE_PROG, src)


def test_attack_exit_0_with_may_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = attack_hub(
        tmp_path,
        '    import prog_atk0 as p\n'
        '    return "b1" in {b.id for b in p.REGISTRY.active()}',
        tag="atk0",
    )
    code, lines, err = run_attack(hub, monkeypatch, capsys, "c_atk")
    assert code == 0, err
    assert lines == ["b1 must_fail failed", "b2 may_pass passed"]


def test_attack_surviving_must_fail_exit_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = attack_hub(tmp_path, "    return True", tag="atk1")
    code, lines, _ = run_attack(hub, monkeypatch, capsys, "c_atk")
    assert code == 3
    assert lines == ["b1 must_fail passed", "b2 may_pass passed"]


def test_attack_baseline_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = attack_hub(tmp_path, "    return False", tag="atk2")
    code, lines, err = run_attack(hub, monkeypatch, capsys, "c_atk")
    assert code == 2
    assert lines == []
    assert "baseline did not pass" in err


def test_attack_unknown_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    hub = attack_hub(tmp_path, "    return True", tag="atk3")
    code, _, err = run_attack(hub, monkeypatch, capsys, "ghost")
    assert code == 2
    assert "unknown claim" in err
