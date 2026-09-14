"""Claims check and attack tests (SPEC §15.2)."""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

import pytest

from helios import claims as claims_lib
from helios import cli
from helios.envelope import Finding

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

HEADER = "import os, sys, time, subprocess\nfrom helios.claims import claim\n"


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


def claim_src(
    name: str,
    body: str,
    covers: str = '("b1",)',
    backend: str = "prover",
    method: str = "proof",
    scope: str = "universal",
    extra: str = "",
) -> str:
    lines = "".join(f"    {ln}\n" for ln in body.split("\n"))
    return (
        f"{HEADER}"
        f'@claim("{name}", covers={covers}, backend="{backend}",'
        f' method="{method}", scope="{scope}"{extra})\n'
        f"def {name}():\n{lines}"
    )


BASE_PROG = """\
from helios.program import Block, Registry
REGISTRY = Registry()
REGISTRY.add(Block("b1", "constraint", "x <= 1"))
REGISTRY.add(Block("b2", "objective", "min x"))
REGISTRY.add(Block("old", "constraint", "z"))
REGISTRY.retire("old")
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
    hub = make_hub(tmp_path, "ok", BASE_PROG, claim_src("c_ok", "return True"))
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
    assert finding["covers"] == ["b1"]
    Finding.model_validate(finding)


def test_check_refuted_exit_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = make_hub(tmp_path, "ref", BASE_PROG, claim_src("c_no", "return False"))
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    assert code == 3
    finding = json.loads(lines[0])
    assert finding["verdict"] == "refuted"
    assert finding["method"] == "counterexample"
    Finding.model_validate(finding)


def test_check_false_without_counterexample_is_inconclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    src = claim_src(
        "c_m",
        "return False",
        backend="meter",
        method="numerical_certificate",
        scope="instance",
        extra=', bound={"instance": "i1"}',
    )
    hub = make_hub(tmp_path, "inc", BASE_PROG, src)
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    assert code == 3
    finding = json.loads(lines[0])
    assert finding["verdict"] == "inconclusive"
    assert finding["bound"] == {"instance": "i1"}
    assert finding["method"] == "numerical_certificate"
    Finding.model_validate(finding)


def test_refuted_finding_never_raises() -> None:
    backends = {"prover": claims_lib.Backend(["proof", "counterexample"], ["universal"])}
    bad = claims_lib.Claim(
        name="x", covers=("b1",), backend="prover", method="proof", scope="bounded"
    )
    finding = claims_lib.refuted_finding(bad, backends)
    assert finding.verdict.value == "inconclusive"
    assert finding.notes != ""


@pytest.mark.parametrize(
    "body,type_name",
    [
        ("return 1", "int"),
        ("return 0", "int"),
        ("return None", "NoneType"),
        ('return "True"', "str"),
        ("return [True]", "list"),
    ],
)
def test_non_bool_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, body: str, type_name: str
) -> None:
    tag = f"nb{abs(hash(body)) % 10000}"
    hub = make_hub(tmp_path, tag, BASE_PROG, claim_src(tag, body))
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    finding = json.loads(lines[0])
    assert code == 3
    assert finding["verdict"] == "inconclusive"
    assert finding["notes"] == f"claim returned {type_name}"
    assert not (hub / ".helios" / "claims" / f"{tag}.traceback.txt").exists()
    Finding.model_validate(finding)


def test_check_error_saves_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = make_hub(
        tmp_path, "er", BASE_PROG, claim_src("c_er", "raise ValueError('boom')")
    )
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    assert code == 3
    finding = json.loads(lines[0])
    assert finding["verdict"] == "inconclusive"
    assert finding["artifact"] == ".helios/claims/c_er.traceback.txt"
    assert "boom" in (hub / ".helios" / "claims" / "c_er.traceback.txt").read_text()
    Finding.model_validate(finding)


def test_check_timeout_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    hub = make_hub(
        tmp_path, "to", BASE_PROG, claim_src("c_to", "time.sleep(30)\nreturn True")
    )
    code, lines, _ = run_check(hub, monkeypatch, capsys, "--timeout", "1")
    assert code == 3
    finding = json.loads(lines[0])
    assert finding["verdict"] == "inconclusive"
    assert finding["notes"] == "timeout after 1 s"
    Finding.model_validate(finding)


def test_timeout_kills_grandchild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    pidfile = tmp_path / "gc.pid"
    monkeypatch.setenv("VERIFY_PIDFILE", str(pidfile))
    body = (
        'p = subprocess.Popen(["sleep", "40"])\n'
        'open(os.environ["VERIFY_PIDFILE"], "w").write(str(p.pid))\n'
        "time.sleep(40)\nreturn True"
    )
    hub = make_hub(tmp_path, "tree", BASE_PROG, claim_src("tree", body))
    code, lines, _ = run_check(hub, monkeypatch, capsys, "--timeout", "2")
    finding = json.loads(lines[0])
    assert code == 3
    assert finding["notes"] == "timeout after 2 s"
    time.sleep(0.5)
    pid = int(pidfile.read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("grandchild survived the timeout")


def test_timeout_note_format() -> None:
    assert claims_lib.timeout_note(600) == "timeout after 600 s"
    assert claims_lib.timeout_note(600.0) == "timeout after 600 s"
    assert claims_lib.timeout_note(2) == "timeout after 2 s"


def test_check_invalid_timeout_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = make_hub(tmp_path, "tmo", BASE_PROG, claim_src("c", "return True"))
    for bad in ("0", "-3"):
        code, _, err = run_check(hub, monkeypatch, capsys, "--timeout", bad)
        assert code == 2
        assert err.startswith("helios: ")


def test_stdout_noise_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    for key, body in [
        ("chat", 'print("solver chatter")\nreturn True'),
        ("nonl", 'print("no newline", end="")\nreturn True'),
        ("big", 'sys.stdout.write("x" * 100000)\nreturn True'),
        ("stderrj", 'print(\'{"result": true}\', file=sys.stderr)\nreturn False'),
    ]:
        hub = make_hub(tmp_path, key, BASE_PROG, claim_src(key, body))
        code, lines, _ = run_check(hub, monkeypatch, capsys)
        finding = json.loads(lines[0])
        assert code == (0 if key != "stderrj" else 3), key
        assert finding["verdict"] == ("verified" if key != "stderrj" else "refuted"), key


def test_forged_output_is_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = make_hub(
        tmp_path,
        "forg",
        BASE_PROG,
        claim_src("forg", 'print(\'{"result": true}\')\nraise SystemExit(1)'),
    )
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    assert code == 3
    finding = json.loads(lines[0])
    assert finding["verdict"] == "inconclusive"
    assert (hub / ".helios" / "claims" / "forg.traceback.txt").exists()


@pytest.mark.parametrize(
    "body,key",
    [
        ("raise SystemExit(0)", "se0"),
        ("raise SystemExit('bye')", "sestr"),
        ("raise KeyboardInterrupt", "kbi"),
        ("os._exit(0)", "osex"),
        ("os.kill(os.getpid(), 9)", "sigk"),
    ],
)
def test_abnormal_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, body: str, key: str
) -> None:
    hub = make_hub(tmp_path, key, BASE_PROG, claim_src(key, body))
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    finding = json.loads(lines[0])
    assert code == 3
    assert finding["verdict"] == "inconclusive"
    Finding.model_validate(finding)
    if key == "sigk":
        assert finding["notes"] == "runner exited -9"


def test_check_finding_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    body = (
        'return Finding(id="other", claim="other", covers=["zzz"],'
        ' verdict="verified", method="proof", scope="universal")'
    )
    src = "from helios.envelope import Finding\n" + claim_src("c_fo", body)
    hub = make_hub(tmp_path, "fo", BASE_PROG, src)
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    assert code == 0
    finding = json.loads(lines[0])
    assert (finding["id"], finding["claim"], finding["covers"]) == ("c_fo", "c_fo", ["b1"])
    assert finding["verdict"] == "verified"


def test_check_finding_disallowed_method(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    body = (
        'return Finding(id="other", claim="other", covers=["zzz"],'
        ' verdict="verified", method="review", scope="universal")'
    )
    src = "from helios.envelope import Finding\n" + claim_src("c_fm", body)
    hub = make_hub(tmp_path, "fm", BASE_PROG, src)
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    assert code == 3
    finding = json.loads(lines[0])
    assert finding["verdict"] == "inconclusive"
    assert finding["covers"] == ["b1"]
    assert "review" in finding["notes"]
    Finding.model_validate(finding)


def test_check_declaration_invalid_step_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    src = claim_src("c_ex", "return True", method="exhaustive_finite_check", scope="universal")
    hub = make_hub(tmp_path, "exu", BASE_PROG, src, backends=None)
    code, lines, err = run_check(hub, monkeypatch, capsys)
    assert code == 2
    assert lines == []
    assert "c_ex" in err


def test_check_bounded_without_bound_step_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    src = claim_src("c_bb", "return False", backend="meter", scope="bounded")
    hub = make_hub(
        tmp_path,
        "bb",
        BASE_PROG,
        src,
        backends='[backend.meter]\nmethods = ["proof"]\nscopes = ["bounded"]\n',
    )
    code, lines, err = run_check(hub, monkeypatch, capsys)
    assert code == 2
    assert lines == []
    assert "c_bb" in err
    assert not (hub / ".helios").exists()


def test_check_manual_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    src = claim_src("c_m", "return None", backend="manual", method="review")
    src += claim_src("c_ok", "return True").split("\n", 2)[2]
    hub = make_hub(tmp_path, "man", BASE_PROG, src)
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    assert code == 0
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == "c_ok"


def test_check_validation_fails_before_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    src = claim_src("c_u", "raise RuntimeError('must not run')", covers="('nope',)")
    src += claim_src("c_r", "return True", covers="('old', 'R2')").split("\n", 2)[2]
    src += claim_src("c_q", "return True", covers="('R1',)").split("\n", 2)[2]
    src += claim_src("c_b", "return True", backend="ghost").split("\n", 2)[2]
    hub = make_hub(tmp_path, "pre", BASE_PROG, src)
    code, lines, err = run_check(hub, monkeypatch, capsys)
    assert code == 2
    assert lines == []
    assert "c_u: unknown block id 'nope'" in err
    assert "c_r: unknown block id 'old'" in err
    assert "c_q: covers no active block" in err
    assert "c_b: unknown backend 'ghost'" in err
    assert not (hub / ".helios").exists()


def test_check_parent_omit_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    body = 'import prog_pom\nreturn "b1" in {b.id for b in prog_pom.REGISTRY.active()}'
    hub = make_hub(
        tmp_path, "pom", BASE_PROG, claim_src("c_pom", body, covers='("b1", "b2")')
    )
    monkeypatch.setenv("HELIOS_OMIT", "b1")
    code, lines, err = run_check(hub, monkeypatch, capsys)
    assert code == 0, err
    assert json.loads(lines[0])["verdict"] == "verified"


def test_check_filters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    src = claim_src("c_a", "return True")
    src += claim_src(
        "c_b",
        "return True",
        covers='("b2",)',
        backend="meter",
        method="numerical_certificate",
        scope="instance",
        extra=', bound={"instance": "i1"}',
    ).split("\n", 2)[2]
    hub = make_hub(tmp_path, "flt", BASE_PROG, src)
    code, lines, _ = run_check(hub, monkeypatch, capsys, "--backend", "meter")
    assert code == 0
    assert [json.loads(line)["id"] for line in lines] == ["c_b"]
    code, lines, _ = run_check(hub, monkeypatch, capsys, "--covers", "b1")
    assert code == 0
    assert [json.loads(line)["id"] for line in lines] == ["c_a"]
    code, lines, _ = run_check(hub, monkeypatch, capsys, "--covers", "nothing")
    assert code == 0
    assert lines == []


def test_check_import_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = tmp_path / "hub-ie"
    hub.mkdir()
    (hub / ".agents").mkdir()
    (hub / ".agents" / "workflow.toml").write_text(
        '[project]\nprogram = "prog_ie"\nclaims = "clm_ie"\n'
    )
    (hub / "prog_ie.py").write_text(BASE_PROG)
    monkeypatch.chdir(hub)
    assert cli.main(["claims", "check"]) == 2
    assert capsys.readouterr().err.startswith("helios: cannot import clm_ie: ")
    (hub / "clm_ie.py").write_text("raise RuntimeError('claims import boom')\n")
    assert cli.main(["claims", "check"]) == 2
    assert (
        capsys.readouterr().err
        == "helios: cannot import clm_ie: RuntimeError: claims import boom\n"
    )


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
        f"{HEADER}"
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


def test_attack_order_skips_and_inconclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    body = (
        "    import prog_atk4 as p\n"
        "    ids = {b.id for b in p.REGISTRY.active()}\n"
        '    if "b2" not in ids: raise RuntimeError("b2 gone")\n'
        '    return "b1" in ids'
    )
    hub = attack_hub(
        tmp_path, body, tag="atk4", covers='("b2", "R9", "old", "b1", "ghost")', redundant="()"
    )
    code, lines, err = run_attack(hub, monkeypatch, capsys, "c_atk")
    assert code == 0, err
    assert lines == ["b2 must_fail inconclusive", "b1 must_fail failed"]


def test_attack_surviving_must_fail_exit_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = attack_hub(tmp_path, "    return True", tag="atk1")
    code, lines, _ = run_attack(hub, monkeypatch, capsys, "c_atk")
    assert code == 3
    assert lines == ["b1 must_fail passed", "b2 may_pass passed"]


def test_attack_parent_omit_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = attack_hub(
        tmp_path,
        '    import prog_atk5 as p\n'
        '    return "b1" in {b.id for b in p.REGISTRY.active()}',
        tag="atk5",
        covers='("b2", "b1")',
        redundant="()",
    )
    monkeypatch.setenv("HELIOS_OMIT", "b1")
    code, lines, err = run_attack(hub, monkeypatch, capsys, "c_atk")
    assert code == 3, err
    assert lines == ["b2 must_fail passed", "b1 must_fail failed"]


def test_attack_baseline_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = attack_hub(tmp_path, "    return False", tag="atk2")
    code, lines, err = run_attack(hub, monkeypatch, capsys, "c_atk")
    assert code == 2
    assert lines == []
    assert "baseline did not pass" in err


@pytest.mark.parametrize(
    "body",
    [
        "    return False",
        "    return 1",
        "    raise RuntimeError()",
        "    from helios.envelope import Finding\n"
        '    return Finding(id="x", claim="x", verdict="verified",'
        ' method="proof", scope="universal")',
    ],
)
def test_attack_baseline_refusals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, body: str
) -> None:
    tag = f"bl{abs(hash(body)) % 10000}"
    hub = attack_hub(tmp_path, body, tag=tag)
    code, lines, err = run_attack(hub, monkeypatch, capsys, "c_atk")
    assert code == 2 and lines == [] and "baseline did not pass" in err


def test_attack_unknown_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    hub = attack_hub(tmp_path, "    return True", tag="atk3")
    code, _, err = run_attack(hub, monkeypatch, capsys, "ghost")
    assert code == 2
    assert "unknown claim" in err


def test_grandchild_reaped_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    pidfile = tmp_path / "gc2.pid"
    monkeypatch.setenv("VERIFY_PIDFILE", str(pidfile))
    body = (
        '    p = subprocess.Popen(["sleep", "8"])\n'
        '    open(os.environ["VERIFY_PIDFILE"], "w").write(str(p.pid))\n'
        "    return True"
    )
    hub = make_hub(tmp_path, "gc2", BASE_PROG, claim_src("gc2", body))
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    assert code == 0
    assert json.loads(lines[0])["verdict"] == "verified"
    pid = int(pidfile.read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raise AssertionError("grandchild outlived the runner")
