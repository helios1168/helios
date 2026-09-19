"""Claims check and attack tests (SPEC §15.2)."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
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

    assert claims_lib.claim_by_name("reg-probe").func is probe
    assert claims_lib.claim_by_name("reg-probe").covers == ("b1",)
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


def test_default_backends_path_resolves_from_the_package_alone(tmp_path: Path) -> None:
    """Prove default_backends_path needs nothing above the helios package, the way a wheel
    install is. Same defect and proof shape as
    tests/test_templates.py::test_path_resolves_from_the_package_alone: the pre-fix
    ``parents[3] / "templates" / "backends.toml"`` only reaches the repository's own
    templates/ directory from a checkout, and is absent once helios is installed from a wheel.

    Builds a stand-in installed layout under tmp_path: a copy of just the modules
    default_backends_path's import chain needs (program, envelope, templates, claims), with
    nothing repository shaped above it. It runs in a subprocess, importing this copy as
    "helios", so the real helios package already imported by this test process cannot mask
    the bug.
    """
    src_helios = Path(__file__).resolve().parents[1] / "src" / "helios"
    site_packages = tmp_path / "site-packages"
    package_dir = site_packages / "helios"
    package_dir.mkdir(parents=True)
    shutil.copyfile(src_helios / "__init__.py", package_dir / "__init__.py")
    shutil.copyfile(src_helios / "program.py", package_dir / "program.py")
    shutil.copyfile(src_helios / "envelope.py", package_dir / "envelope.py")
    shutil.copytree(src_helios / "templates", package_dir / "templates")
    shutil.copytree(src_helios / "claims", package_dir / "claims")

    # Nothing three directories above the stand-in claims module holds a templates/
    # directory; that is exactly what the pre-fix parents[3] lookup depended on.
    assert not (site_packages / "templates").exists()

    probe = "from helios.claims import default_backends_path\nprint(default_backends_path())"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(site_packages)},
        capture_output=True,
        text=True,
        check=True,
    )
    resolved = Path(result.stdout.strip())
    assert resolved == package_dir / "templates" / "backends.toml"
    assert resolved.is_file()


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
    start = time.monotonic()
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    secs = time.monotonic() - start
    assert code == 0
    assert json.loads(lines[0])["verdict"] == "verified"
    assert secs < 4, secs
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


FORGE_LINE = '\'{"result": true}\\n\''


@pytest.mark.parametrize(
    "key,body,verdict",
    [
        ("dunder", f"sys.__stdout__.write({FORGE_LINE})\nreturn False", "refuted"),
        (
            "fdopen",
            f"f = os.fdopen(1, 'w', closefd=False)\nf.write({FORGE_LINE})\nf.flush()\nreturn False",
            "refuted",
        ),
        (
            "devout",
            f"f = open('/dev/stdout', 'w')\nf.write({FORGE_LINE})\nf.close()\nreturn False",
            "refuted",
        ),
        (
            "devfd",
            f"f = open('/dev/fd/1', 'w')\nf.write({FORGE_LINE})\nf.close()\nreturn False",
            "refuted",
        ),
        ("oswrite", f"os.write(1, {FORGE_LINE}.encode())\nreturn False", "refuted"),
        ("closefd", "print('noise')\nos.close(1)\nreturn True", "verified"),
        (
            "dupnull",
            "n = os.open(os.devnull, os.O_WRONLY)\nos.dup2(n, 1)\nprint('x')\nreturn True",
            "verified",
        ),
        (
            "fork",
            f"pid = os.fork()\nif pid == 0:\n    time.sleep(1.0)\n"
            f"    os.write(1, {FORGE_LINE}.encode())\n    os._exit(0)\nreturn False",
            "refuted",
        ),
        (
            "rebinderr",
            "sys.stdout = sys.stderr\nprint('solver log')\nreturn True",
            "verified",
        ),
        (
            "rebindfile",
            "sys.stdout = open('solver.log', 'w')\nprint('solver log')\nreturn True",
            "verified",
        ),
        ("closestdout", "sys.stdout.close()\nreturn True", "verified"),
        (
            "cprintf",
            "import ctypes\nctypes.CDLL(None).printf(b'C solver log line\\n')\nreturn True",
            "verified",
        ),
        (
            "atexit",
            "import atexit, io\nsys.stdout = io.StringIO()\n"
            f"atexit.register(os.write, 1, {FORGE_LINE}.encode())\nreturn False",
            "refuted",
        ),
    ],
)
def test_runner_forgery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, key: str, body: str, verdict: str
) -> None:
    hub = make_hub(tmp_path, f"fg{key}", BASE_PROG, claim_src(f"fg{key}", body))
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    finding = json.loads(lines[0])
    assert finding["verdict"] == verdict, (key, finding)
    assert code == (0 if verdict == "verified" else 3)
    Finding.model_validate(finding)


def test_unparsable_note_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = make_hub(tmp_path, "osex", BASE_PROG, claim_src("osex", "os._exit(0)"))
    code, lines, _ = run_check(hub, monkeypatch, capsys)
    finding = json.loads(lines[0])
    assert code == 3
    assert finding["verdict"] == "inconclusive"
    assert finding["notes"] == "unparsable runner output"


def test_grandchild_holding_pipes_fast_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    pidfile = tmp_path / "gc3.pid"
    monkeypatch.setenv("VERIFY_PIDFILE", str(pidfile))
    body = (
        'p = subprocess.Popen(["sleep", "13"])\n'
        'open(os.environ["VERIFY_PIDFILE"], "w").write(str(p.pid))\nreturn True'
    )
    hub = make_hub(tmp_path, "gc3", BASE_PROG, claim_src("gc3", body))
    start = time.monotonic()
    code, lines, _ = run_check(hub, monkeypatch, capsys, "--timeout", "5")
    secs = time.monotonic() - start
    finding = json.loads(lines[0])
    assert code == 0
    assert finding["verdict"] == "verified"
    assert secs < 4, secs
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


@pytest.mark.parametrize(
    "src", ["raise SystemExit(0)\n", "raise SystemExit('x')\n", "raise KeyboardInterrupt\n"]
)
def test_import_base_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, src: str
) -> None:
    hub = tmp_path / f"hub-ibe{abs(hash(src)) % 997}"
    hub.mkdir()
    (hub / ".agents").mkdir()
    (hub / ".agents" / "workflow.toml").write_text(
        '[project]\nprogram = "prog_ibe"\nclaims = "clm_ibe"\n'
    )
    (hub / "prog_ibe.py").write_text(BASE_PROG)
    (hub / "clm_ibe.py").write_text(claim_src("c", "return True"))
    (hub / "clm_ibe.py").write_text(src)
    monkeypatch.chdir(hub)
    assert cli.main(["claims", "check"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("helios: cannot import clm_ibe: "), err


def test_step2_single_line_per_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    src = claim_src(
        "c_bb",
        "return True",
        backend="meter",
        method="numerical_certificate",
        scope="instance",
    )
    hub = make_hub(tmp_path, "sl", BASE_PROG, src)
    code, lines, err = run_check(hub, monkeypatch, capsys)
    assert code == 2
    assert lines == []
    assert len(err.splitlines()) == 1
    assert err.startswith("c_bb: ")


def test_claims_package_submodule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = make_hub(tmp_path, "pkg", BASE_PROG, claim_src("pack", "return True"))
    (hub / "clm_pkg.py").unlink()
    (hub / "clm_pkg").mkdir()
    (hub / "clm_pkg" / "__init__.py").write_text("from clm_pkg import alg\n")
    (hub / "clm_pkg" / "alg.py").write_text(HEADER + claim_src("subc", "return False"))
    monkeypatch.chdir(hub)
    assert cli.main(["claims", "check"]) == 3
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1
    finding = json.loads(out[0])
    assert (finding["id"], finding["verdict"]) == ("subc", "refuted")


def test_duplicate_claim_names_step1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    hub = make_hub(tmp_path, "dup", BASE_PROG, claim_src("same", "return True"))
    (hub / "clm_dup.py").unlink()
    (hub / "clm_dup").mkdir()
    (hub / "clm_dup" / "__init__.py").write_text("from clm_dup import one, two\n")
    (hub / "clm_dup" / "one.py").write_text(HEADER + claim_src("same", "return True"))
    (hub / "clm_dup" / "two.py").write_text(HEADER + claim_src("same", "return False"))
    monkeypatch.chdir(hub)
    assert cli.main(["claims", "check"]) == 2
    err = capsys.readouterr().err
    assert err == "same: duplicate claim name\n"


# ============================================================ round 3 review fixes


def test_ctypes_unavailable_in_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The runner flushes C stdio best-effort; ctypes unavailable must not fail the claim."""
    block = tmp_path / "blk"
    block.mkdir()
    (block / "sitecustomize.py").write_text(
        "import sys\n"
        'if "helios.claims.runner" in sys.orig_argv:\n'
        "    class _Block:\n"
        "        def find_spec(self, name, path=None, target=None):\n"
        '            if name in ("ctypes", "_ctypes") or name.startswith("ctypes."):\n'
        '                raise ImportError("ctypes blocked by verifier")\n'
        "            return None\n"
        "    sys.meta_path.insert(0, _Block())\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(block))
    hub = make_hub(tmp_path, "noct", BASE_PROG, claim_src("noct", "return True"))
    code, lines, err = run_check(hub, monkeypatch, capsys)
    assert code == 0, err
    assert json.loads(lines[0])["verdict"] == "verified"


def test_setsid_grandchild_returns_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A grandchild that left the runner's process group must not block collection."""
    pidfile = tmp_path / "ss.pid"
    monkeypatch.setenv("VERIFY_PIDFILE", str(pidfile))
    body = (
        'p = subprocess.Popen(["sleep", "25"], start_new_session=True)\n'
        'open(os.environ["VERIFY_PIDFILE"], "w").write(str(p.pid))\nreturn True'
    )
    hub = make_hub(tmp_path, "ssgc", BASE_PROG, claim_src("ssgc", body))
    start = time.monotonic()
    code, lines, err = run_check(hub, monkeypatch, capsys, "--timeout", "30")
    secs = time.monotonic() - start
    assert code == 0, err
    assert json.loads(lines[0])["verdict"] == "verified"
    assert secs < 5, secs
    try:
        os.kill(int(pidfile.read_text()), signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_setsid_grandchild_timeout_returns_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A timed-out claim with a setsid grandchild still returns within the collection bound."""
    pidfile = tmp_path / "sst.pid"
    monkeypatch.setenv("VERIFY_PIDFILE", str(pidfile))
    body = (
        'p = subprocess.Popen(["sleep", "25"], start_new_session=True)\n'
        'open(os.environ["VERIFY_PIDFILE"], "w").write(str(p.pid))\n'
        "time.sleep(60)\nreturn True"
    )
    hub = make_hub(tmp_path, "sstgc", BASE_PROG, claim_src("sstgc", body))
    start = time.monotonic()
    code, lines, _ = run_check(hub, monkeypatch, capsys, "--timeout", "2")
    secs = time.monotonic() - start
    finding = json.loads(lines[0])
    assert code == 3
    assert finding["notes"] == "timeout after 2 s"
    assert secs < 7, secs
    try:
        os.kill(int(pidfile.read_text()), signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_reimport_clears_stale_records(tmp_path: Path) -> None:
    """Re-loading the same claims module forgets its stale records first (SPEC §15.2, decided)."""
    hub = make_hub(tmp_path, "reim", BASE_PROG, claim_src("c_reim", "return True"))
    claims_lib.load_claims(hub, "clm_reim")
    claims_lib.load_claims(hub, "clm_reim")
    assert len(claims_lib.records_for("clm_reim")) == 1


def test_duplicate_detected_before_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Duplicate names are a step 1 problem before --backend or --covers narrow the set."""
    hub = make_hub(tmp_path, "dupf", BASE_PROG, "")
    (hub / "clm_dupf.py").unlink()
    (hub / "clm_dupf").mkdir()
    (hub / "clm_dupf" / "__init__.py").write_text("from clm_dupf import a, b\n")
    (hub / "clm_dupf" / "a.py").write_text(
        HEADER + claim_src("fb", "return False", backend="meter",
                           method="numerical_certificate", scope="instance",
                           extra=', bound={"instance": "i1"}')
    )
    (hub / "clm_dupf" / "b.py").write_text(HEADER + claim_src("fb", "return True"))
    monkeypatch.chdir(hub)
    assert cli.main(["claims", "check", "--backend", "meter"]) == 2
    err = capsys.readouterr().err
    assert err == "fb: duplicate claim name\n"


def test_foreign_module_never_shadows_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A claim registered by a module outside the configured package never runs instead."""
    hub = make_hub(tmp_path, "frn", BASE_PROG, "")
    (hub / "clm_frn.py").unlink()
    (hub / "clm_frn").mkdir()
    (hub / "clm_frn" / "__init__.py").write_text(
        claim_src("xn", "return False") + "import shared_frn  # noqa: F401\n"
    )
    (hub / "shared_frn.py").write_text(claim_src("xn", "return True"))
    monkeypatch.chdir(hub)
    code = cli.main(["claims", "check"])
    out = capsys.readouterr().out.splitlines()
    assert code == 3
    finding = json.loads(out[0])
    assert finding["verdict"] == "refuted"


def test_unimported_submodule_not_collected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A submodule the package never imports contributes no claim (SPEC §15.2, decided)."""
    hub = make_hub(tmp_path, "lone", BASE_PROG, "")
    (hub / "clm_lone.py").unlink()
    (hub / "clm_lone").mkdir()
    (hub / "clm_lone" / "__init__.py").write_text(claim_src("top", "return True"))
    (hub / "clm_lone" / "lonely.py").write_text(claim_src("lonely", "return False"))
    monkeypatch.chdir(hub)
    assert cli.main(["claims", "check"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [json.loads(line)["id"] for line in out] == ["top"]


@pytest.mark.parametrize(
    "bad_name,tag",
    [("a\nb", "bnnl"), ("", "bnem"), ("has/slash", "bnsl"), ("x" * 201, "bnlong")],
)
def test_invalid_claim_name_rejected(
    bad_name: str, tag: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A claim name outside ^[A-Za-z0-9_][A-Za-z0-9._-]{0,199}$ fails at registration."""
    src = (
        f"{HEADER}"
        f"@claim({bad_name!r}, covers=('b1',), backend='prover', method='proof',"
        " scope='universal')\n"
        "def f():\n    return True\n"
    )
    hub = make_hub(tmp_path, tag, BASE_PROG, src)
    monkeypatch.chdir(hub)
    assert cli.main(["claims", "check"]) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"helios: cannot import clm_{tag}: ValueError: "), err


# ============================================================ round 4 review fixes


DECL = 'covers=("b1",), backend="prover", method="proof", scope="universal"'


def verdicts_of(lines: list[str]) -> dict[str, str]:
    return {(f := json.loads(line))["id"]: f["verdict"] for line in lines}


def test_one_line_lambdas_same_name_is_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Two lambdas on one line sharing a claim name are the step 1 duplicate (decided)."""
    src = (
        f"{HEADER}"
        f'claim("la", {DECL})(lambda: True)\n'
        f'claim("la", {DECL})(lambda: False)\n'
    )
    hub = make_hub(tmp_path, "lam", BASE_PROG, src)
    monkeypatch.chdir(hub)
    assert cli.main(["claims", "check"]) == 2
    assert capsys.readouterr().err == "la: duplicate claim name\n"


def test_one_line_lambdas_distinct_names_both_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Two lambdas on one line with distinct names both run (module is the call site)."""
    src = (
        f"{HEADER}"
        f'claim("la2", {DECL})(lambda: True)\n'
        f'claim("lb2", {DECL})(lambda: False)\n'
    )
    hub = make_hub(tmp_path, "lam2", BASE_PROG, src)
    monkeypatch.chdir(hub)
    assert cli.main(["claims", "check"]) == 3
    assert verdicts_of(capsys.readouterr().out.splitlines()) == {
        "la2": "verified",
        "lb2": "refuted",
    }


def test_factory_same_name_is_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A factory called twice with the same claim name is a duplicate (decided).

    func.__qualname__ and co_firstlineno are identical for both inner
    functions; the module is the call site, keyed only with the claim name.
    """
    src = HEADER + textwrap.dedent(
        f"""\
        def make(name, value):
            @claim(name, {DECL})
            def f():
                return value
            return f
        make("fs", False)
        make("fs", True)
        """
    )
    hub = make_hub(tmp_path, "facs", BASE_PROG, src)
    monkeypatch.chdir(hub)
    assert cli.main(["claims", "check"]) == 2
    assert capsys.readouterr().err == "fs: duplicate claim name\n"


def test_factory_distinct_names_both_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A factory producing distinct names works with the right verdicts (decided)."""
    src = HEADER + textwrap.dedent(
        f"""\
        def make(name, value):
            @claim(name, {DECL})
            def f():
                return value
            return f
        make("fa", True)
        make("fb", False)
        """
    )
    hub = make_hub(tmp_path, "fac", BASE_PROG, src)
    monkeypatch.chdir(hub)
    assert cli.main(["claims", "check"]) == 3
    assert verdicts_of(capsys.readouterr().out.splitlines()) == {
        "fa": "verified",
        "fb": "refuted",
    }


def test_wraps_pair_same_name_is_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Two functools.wraps wrappers of functions named check sharing a name are duplicates."""
    src = HEADER + "import functools\n" + textwrap.dedent(
        f"""\
        def logged(fn):
            @functools.wraps(fn)
            def wrapper():
                return fn()
            return wrapper

        @claim("wd", {DECL})
        @logged
        def check():
            return False

        @claim("wd", {DECL})
        @logged
        def check():
            return True
        """
    )
    hub = make_hub(tmp_path, "wrpd", BASE_PROG, src)
    monkeypatch.chdir(hub)
    assert cli.main(["claims", "check"]) == 2
    assert capsys.readouterr().err == "wd: duplicate claim name\n"


def test_exec_pair_same_name_is_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Two exec blocks registering the same claim name are duplicates (decided)."""
    ea = f'@claim("ea2", {DECL})\ndef f():\n    return True\n'
    eb = f'@claim("ea2", {DECL})\ndef f():\n    return False\n'
    src = HEADER + f"exec({ea!r}, globals())\nexec({eb!r}, globals())\n"
    hub = make_hub(tmp_path, "exed", BASE_PROG, src)
    monkeypatch.chdir(hub)
    assert cli.main(["claims", "check"]) == 2
    assert capsys.readouterr().err == "ea2: duplicate claim name\n"


def test_callable_instance_and_partial_are_valid_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A callable instance and a functools.partial each run with the right verdict (decided)."""
    src = HEADER + "import functools\n" + textwrap.dedent(
        f"""\
        class Check:
            def __call__(self):
                return True
        claim("inst2", {DECL})(Check())
        def g(v):
            return v
        claim("part2", {DECL})(functools.partial(g, True))
        """
    )
    hub = make_hub(tmp_path, "inst2", BASE_PROG, src)
    monkeypatch.chdir(hub)
    assert cli.main(["claims", "check"]) == 0
    assert verdicts_of(capsys.readouterr().out.splitlines()) == {
        "inst2": "verified",
        "part2": "verified",
    }


def test_pump_caps_stdout_head_and_flags_overflow() -> None:
    """Stdout (the protocol channel) keeps at most STDOUT_CAP bytes past which overflow is set."""
    r, w = os.pipe()
    buf = bytearray()
    overflow = [False]
    data = b"x" * (claims_lib.STDOUT_CAP + 4096)

    def writer() -> None:
        written = 0
        while written < len(data):
            written += os.write(w, data[written:])
        os.close(w)

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    claims_lib._pump(r, buf, claims_lib.STDOUT_CAP, False, overflow, threading.Event())
    t.join(10)
    os.close(r)
    assert len(buf) == claims_lib.STDOUT_CAP
    assert overflow[0] is True


def test_pump_caps_stderr_to_last_bytes() -> None:
    """Stderr keeps only the last STDERR_CAP bytes, never growing past it (SPEC §15.2, decided)."""
    r, w = os.pipe()
    buf = bytearray()
    overflow = [False]
    chunk = b"y" * claims_lib.STDERR_CAP
    tail = b"z" * 100

    def writer() -> None:
        for _ in range(3):
            written = 0
            while written < len(chunk):
                written += os.write(w, chunk[written:])
        written = 0
        while written < len(tail):
            written += os.write(w, tail[written:])
        os.close(w)

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    claims_lib._pump(r, buf, claims_lib.STDERR_CAP, True, overflow, threading.Event())
    t.join(10)
    os.close(r)
    assert len(buf) == claims_lib.STDERR_CAP
    assert bytes(buf).endswith(tail)
    assert overflow[0] is False


def test_setsid_yes_grandchild_output_capped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A setsid `yes` writing to inherited stdout and stderr must not balloon memory (decided)."""
    pidfile = tmp_path / "yes.pid"
    monkeypatch.setenv("VERIFY_PIDFILE", str(pidfile))
    body = (
        'p = subprocess.Popen(["yes"], start_new_session=True)\n'
        'open(os.environ["VERIFY_PIDFILE"], "w").write(str(p.pid))\n'
        "time.sleep(0.5)\nreturn True"
    )
    hub = make_hub(tmp_path, "yesgc", BASE_PROG, claim_src("yesgc", body))
    start = time.monotonic()
    code, lines, err = run_check(hub, monkeypatch, capsys, "--timeout", "30")
    secs = time.monotonic() - start
    assert code == 0, err
    assert json.loads(lines[0])["verdict"] == "verified"
    assert secs < 5, secs
    try:
        os.kill(int(pidfile.read_text()), signal.SIGKILL)
    except ProcessLookupError:
        pass


# ============================================================ round 5 review fixes


def test_exec_bare_namespace_in_package_module_collected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """exec(SRC, {"claim": claim}) inside a package module is still collected.

    The exec frame's globals lack __name__; _caller_module must skip that
    frame and keep walking outward to the enclosing named package module
    (decided) instead of attributing the claim to module name "".
    """
    hub = make_hub(tmp_path, "exb", BASE_PROG, "")
    (hub / "clm_exb.py").unlink()
    (hub / "clm_exb").mkdir()
    body = f'@claim("exb1", {DECL})\ndef f():\n    return False\n'
    (hub / "clm_exb" / "__init__.py").write_text(
        HEADER + f"exec({body!r}, {{'claim': claim}})\n"
    )
    monkeypatch.chdir(hub)
    assert cli.main(["claims", "check"]) == 3
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1
    finding = json.loads(out[0])
    assert (finding["id"], finding["verdict"]) == ("exb1", "refuted")


def test_exec_namespace_with_module_name_still_collected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """exec(SRC, {"claim": claim, "__name__": <module>}) still works (regression)."""
    hub = make_hub(tmp_path, "exn", BASE_PROG, "")
    (hub / "clm_exn.py").unlink()
    (hub / "clm_exn").mkdir()
    body = f'@claim("exn1", {DECL})\ndef f():\n    return True\n'
    (hub / "clm_exn" / "__init__.py").write_text(
        HEADER + f"exec({body!r}, {{'claim': claim, '__name__': __name__}})\n"
    )
    monkeypatch.chdir(hub)
    assert cli.main(["claims", "check"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1
    finding = json.loads(out[0])
    assert (finding["id"], finding["verdict"]) == ("exn1", "verified")


def test_pump_stops_after_next_read_once_signaled() -> None:
    """A pump exits at its next return from os.read once stop is set.

    No further buffering or looping happens afterward: buf stops growing past
    whatever size it held right when the flag was noticed (the abandoned-pump
    CPU fix).
    """
    r, w = os.pipe()
    buf = bytearray()
    overflow = [False]
    stop = threading.Event()
    keep_writing = threading.Event()
    keep_writing.set()

    def writer() -> None:
        while keep_writing.is_set():
            try:
                os.write(w, b"y" * 4096)
            except OSError:
                return

    writer_thread = threading.Thread(target=writer, daemon=True)
    writer_thread.start()
    pump = threading.Thread(
        target=claims_lib._pump, args=(r, buf, 1 << 20, True, overflow, stop), daemon=True
    )
    pump.start()
    time.sleep(0.05)
    stop.set()
    pump.join(timeout=1.0)
    assert not pump.is_alive()
    size_at_stop = len(buf)
    time.sleep(0.2)
    assert len(buf) == size_at_stop
    keep_writing.clear()
    writer_thread.join(timeout=2.0)
    os.close(w)
    os.close(r)


def test_abandoned_pump_stops_burning_cpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """An abandoned pump reading a setsid `yes` on inherited stderr exits soon after the
    collection bound, and a later claim in the same invocation still runs correctly.

    Thread instances are captured at creation (a monkeypatched Thread subclass), not
    found afterward by scanning threading.enumerate(), so the assertion holds even
    when a pump has already exited by the time the check below runs.
    """
    pidfile = tmp_path / "burn.pid"
    monkeypatch.setenv("VERIFY_PIDFILE", str(pidfile))
    body_yes = (
        'p = subprocess.Popen(["yes"], start_new_session=True)\n'
        'open(os.environ["VERIFY_PIDFILE"], "w").write(str(p.pid))\n'
        "time.sleep(0.2)\nreturn True"
    )
    src = claim_src("burn_yes", body_yes)
    src += claim_src("burn_after", "return True").split("\n", 2)[2]
    hub = make_hub(tmp_path, "burn", BASE_PROG, src)

    created: list[threading.Thread] = []
    real_thread = threading.Thread

    class RecordingThread(real_thread):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            if kwargs.get("name", "").startswith("helios-pump-"):
                created.append(self)

    monkeypatch.setattr(claims_lib.threading, "Thread", RecordingThread)
    code, lines, err = run_check(hub, monkeypatch, capsys)
    assert code == 0, err
    assert verdicts_of(lines) == {"burn_yes": "verified", "burn_after": "verified"}
    assert len(created) >= 2, "expected pump threads for both claims"
    for t in created:
        t.join(timeout=1.0)
        assert not t.is_alive(), t.name
    try:
        os.kill(int(pidfile.read_text()), signal.SIGKILL)
    except ProcessLookupError:
        pass
