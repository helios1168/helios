"""Claim registry, backends and check/attack orchestration (SPEC §15.2)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TextIO

from helios import program as prog
from helios.envelope import Finding, Method, Scope, Verdict

REQUIREMENT_RE = re.compile(r"^R[0-9]+$")
MANUAL_BACKEND = "manual"


def is_requirement(id: str) -> bool:
    """An id matching ^R[0-9]+$ is a requirement id (SPEC §15.2 step 1)."""
    return REQUIREMENT_RE.match(id) is not None


@dataclass
class Claim:
    """One registered claim (SPEC §15.2)."""

    name: str
    covers: tuple[str, ...] = ()
    backend: str = ""
    method: str = ""
    scope: str = ""
    bound: dict[str, str] | None = None
    artifact: str | None = None
    redundant: tuple[str, ...] = ()
    func: Callable[[], Any] | None = None
    module: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


_CLAIMS: dict[str, Claim] = {}


def claim(
    name: str,
    covers: tuple[str, ...] | list[str],
    backend: str,
    method: str,
    scope: str,
    bound: dict[str, str] | None = None,
    artifact: str | None = None,
    redundant: tuple[str, ...] | list[str] = (),
) -> Callable[[Callable[[], Any]], Callable[[], Any]]:
    """Register a zero-argument claim callable (SPEC §15.2)."""

    def decorator(func: Callable[[], Any]) -> Callable[[], Any]:
        _CLAIMS[name] = Claim(
            name=name,
            covers=tuple(covers),
            backend=backend,
            method=method,
            scope=scope,
            bound=bound,
            artifact=artifact,
            redundant=tuple(redundant),
            func=func,
            module=func.__module__,
        )
        return func

    return decorator


def claim_by_name(name: str) -> Claim:
    """Return the registered claim, raising KeyError when unknown."""
    return _CLAIMS[name]


@dataclass
class Backend:
    """Allowed methods and scopes for one backend."""

    methods: list[str]
    scopes: list[str]


def default_backends_path() -> Path:
    """templates/backends.toml relative to the repository root."""
    return Path(__file__).resolve().parents[3] / "templates" / "backends.toml"


def backends_path(hub: Path) -> Path:
    """The project .agents/backends.toml, else templates/backends.toml (SPEC §15.2)."""
    project = hub / ".agents" / "backends.toml"
    if project.is_file():
        return project
    return default_backends_path()


def load_backends(hub: Path) -> dict[str, Backend]:
    """Read backends and their allowed methods and scopes (SPEC §15.2)."""
    path = backends_path(hub)
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise ValueError(f"backends file not found: {path}") from None
    out: dict[str, Backend] = {}
    table = raw.get("backend", {})
    if not isinstance(table, dict):
        raise ValueError(f"backends file {path} has no [backend.*] tables")
    for name, entry in table.items():
        methods = entry.get("methods", []) if isinstance(entry, dict) else []
        scopes = entry.get("scopes", []) if isinstance(entry, dict) else []
        out[str(name)] = Backend(methods=list(methods), scopes=list(scopes))
    return out


def load_claims(hub: Path, module_name: str | None) -> list[Claim]:
    """Import the claims module and return its claims sorted by name."""
    if not module_name:
        raise ValueError("project.claims is not configured")
    module = prog.load_module(module_name, hub)
    return sorted(
        (c for c in _CLAIMS.values() if c.module == module.__name__),
        key=lambda c: c.name,
    )


def validate_claims(
    claims: list[Claim], active_ids: set[str], backends: dict[str, Backend]
) -> list[str]:
    """Coverage and backend problems for every selected claim (SPEC §15.2 steps 1-2)."""
    problems: list[str] = []
    for c in claims:
        covered = [i for i in c.covers if not is_requirement(i) and i in active_ids]
        for i in c.covers:
            if is_requirement(i) or i in active_ids:
                continue
            problems.append(f"{c.name}: unknown block id {i!r}")
        if not covered:
            problems.append(f"{c.name}: covers no active block")
        backend = backends.get(c.backend)
        if backend is None:
            problems.append(f"{c.name}: unknown backend {c.backend!r}")
            continue
        if c.method not in backend.methods:
            problems.append(f"{c.name}: backend {c.backend!r} does not allow method {c.method!r}")
        if c.scope not in backend.scopes:
            problems.append(f"{c.name}: backend {c.backend!r} does not allow scope {c.scope!r}")
    return problems


def run_claim(
    hub: Path, module_name: str, name: str, timeout: float, omit: str | None = None
) -> tuple[str, Any]:
    """Run one claim in a subprocess; return (status, payload) (SPEC §15.2 step 3).

    Status is one of timeout, ok (payload is a bool), other (a JSON non-bool),
    finding (payload is a dict), error (payload is a traceback string) or
    unparsable (payload is the raw stdout).
    """
    env = dict(os.environ)
    if omit is None:
        env.pop(prog.OMIT_ENV, None)
    else:
        env[prog.OMIT_ENV] = omit
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "helios.claims.runner", module_name, name],
            cwd=hub,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ("timeout", None)
    lines = [line for line in proc.stdout.split("\n") if line.strip()]
    if not lines:
        return ("unparsable", proc.stdout)
    try:
        message = json.loads(lines[-1])
    except json.JSONDecodeError:
        return ("unparsable", proc.stdout)
    if not isinstance(message, dict):
        return ("unparsable", proc.stdout)
    if "error" in message:
        return ("error", message["error"])
    if "finding" in message:
        return ("finding", message["finding"])
    if "result" in message:
        result = message["result"]
        if isinstance(result, bool):
            return ("ok", result)
        return ("other", result)
    return ("unparsable", proc.stdout)


def _safe_finding(
    *,
    name: str,
    covers: list[str],
    verdict: Verdict,
    method: str,
    scope: str,
    bound: dict[str, str] | None,
    artifact: str | None,
    notes: str,
) -> Finding:
    """Build a Finding, falling back to a minimal valid shape when invalid."""
    try:
        return Finding(
            id=name,
            claim=name,
            covers=covers,
            verdict=verdict,
            method=Method(method),
            scope=Scope(scope),
            bound=bound,
            artifact=artifact,
            notes=notes,
        )
    except ValueError:
        return Finding(
            id=name,
            claim=name,
            covers=covers,
            verdict=verdict,
            method=Method.REVIEW,
            scope=Scope.UNIVERSAL,
            notes=notes,
        )


def verified_finding(c: Claim) -> Finding:
    """A verified finding carrying the declared fields (SPEC §15.2 step 3)."""
    return _safe_finding(
        name=c.name,
        covers=list(c.covers),
        verdict=Verdict.VERIFIED,
        method=c.method,
        scope=c.scope,
        bound=c.bound,
        artifact=c.artifact,
        notes="",
    )


def inconclusive_finding(c: Claim, note: str, artifact: str | None = None) -> Finding:
    """An inconclusive finding keeping the declared fields plus a note."""
    return _safe_finding(
        name=c.name,
        covers=list(c.covers),
        verdict=Verdict.INCONCLUSIVE,
        method=c.method,
        scope=c.scope,
        bound=c.bound,
        artifact=artifact if artifact is not None else c.artifact,
        notes=note,
    )


def refuted_finding(c: Claim, backends: dict[str, Backend]) -> Finding | None:
    """A refuted finding with method counterexample, else None (SPEC §15.2 step 3)."""
    backend = backends.get(c.backend)
    if backend is None or Method.COUNTEREXAMPLE.value not in backend.methods:
        return None
    return _safe_finding(
        name=c.name,
        covers=list(c.covers),
        verdict=Verdict.REFUTED,
        method=Method.COUNTEREXAMPLE.value,
        scope=c.scope,
        bound=c.bound,
        artifact=c.artifact,
        notes="",
    )


def timeout_note(timeout: float) -> str:
    """The timeout note of SPEC §15.2 step 3."""
    return f"timeout after {timeout:g} s"


def check_claims(
    *,
    hub: Path,
    program: str | None,
    claims: str | None,
    backend: str | None = None,
    covers: str | None = None,
    timeout: float = 600.0,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Run `helios claims check` (SPEC §15.2); return the exit code."""
    stdout = out if out is not None else sys.stdout
    stderr = err if err is not None else sys.stderr
    backends = load_backends(hub)
    _, registry = prog.load_registry(hub, program)
    if not claims:
        raise ValueError("project.claims is not configured")
    selected = load_claims(hub, claims)
    if backend is not None:
        selected = [c for c in selected if c.backend == backend]
    if covers is not None:
        selected = [c for c in selected if covers in c.covers]
    active_ids = {b.id for b in registry.active()}
    problems = validate_claims(selected, active_ids, backends)
    if problems:
        for problem in problems:
            print(problem, file=stderr)
        return 2
    findings: list[Finding] = []
    for c in selected:
        if c.backend == MANUAL_BACKEND:
            continue
        findings.append(run_selected(hub, claims, c, backends, timeout))
    for finding in findings:
        print(json.dumps(finding.model_dump(mode="json"), sort_keys=True), file=stdout)
    if all(f.verdict is Verdict.VERIFIED for f in findings):
        return 0
    return 3


def run_selected(
    hub: Path, module_name: str, c: Claim, backends: dict[str, Backend], timeout: float
) -> Finding:
    """Run one non-manual claim and map the runner outcome to a finding."""
    status, payload = run_claim(hub, module_name, c.name, timeout)
    if status == "ok" and payload is True:
        return verified_finding(c)
    if status == "ok":
        refuted = refuted_finding(c, backends)
        if refuted is not None:
            return refuted
        return inconclusive_finding(
            c, f"backend {c.backend!r} does not allow counterexample evidence"
        )
    if status == "finding":
        try:
            return Finding.model_validate(payload)
        except ValueError as exc:
            return inconclusive_finding(c, f"claim returned an invalid finding: {exc}")
    if status == "error":
        path = hub / ".helios" / "claims" / f"{c.name}.traceback.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(payload))
        artifact = str(path.relative_to(hub))
        return inconclusive_finding(c, f"claim raised; traceback saved to {artifact}", artifact)
    if status == "timeout":
        return inconclusive_finding(c, timeout_note(timeout))
    if status == "other":
        return inconclusive_finding(c, f"claim returned non-boolean result {payload!r}")
    return inconclusive_finding(c, f"could not parse runner output: {str(payload)[:200]!r}")


def attack_claim(
    *,
    hub: Path,
    program: str | None,
    claims: str | None,
    name: str,
    timeout: float = 600.0,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Run `helios claims attack <claim>` (SPEC §15.2); return the exit code."""
    stdout = out if out is not None else sys.stdout
    stderr = err if err is not None else sys.stderr
    _, registry = prog.load_registry(hub, program)
    if not claims:
        raise ValueError("project.claims is not configured")
    selected = load_claims(hub, claims)
    wanted = [c for c in selected if c.name == name]
    if not wanted:
        print(f"{name}: unknown claim", file=stderr)
        return 2
    c = wanted[0]
    status, payload = run_claim(hub, claims, c.name, timeout)
    if status != "ok" or payload is not True:
        print("baseline did not pass", file=stderr)
        return 2
    active_ids = {b.id for b in registry.active()}
    survived = False
    for covered in c.covers:
        if is_requirement(covered) or covered not in active_ids:
            continue
        expected = "may_pass" if covered in c.redundant else "must_fail"
        status, payload = run_claim(hub, claims, c.name, timeout, omit=covered)
        if status == "ok" and payload is True:
            outcome = "passed"
        elif status == "ok":
            outcome = "failed"
        else:
            outcome = "inconclusive"
        print(f"{covered} {expected} {outcome}", file=stdout)
        if expected == "must_fail" and outcome == "passed":
            survived = True
    return 3 if survived else 0
