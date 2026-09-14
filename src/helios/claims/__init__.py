"""Claim registry, backends and check/attack orchestration (SPEC §15.2)."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TextIO

from helios import program as prog
from helios.envelope import Finding, Method, Scope, Verdict
from pydantic import ValidationError

REQUIREMENT_RE = re.compile(r"^R[0-9]+$")
MANUAL_BACKEND = "manual"
COLLECT_S = 2.0


class scrubbed_omit:
    """Remove HELIOS_OMIT from the environment, restoring it after (SPEC §15.2)."""

    def __enter__(self) -> None:
        self._saved: str | None = os.environ.pop(prog.OMIT_ENV, None)

    def __exit__(self, *exc: Any) -> None:
        if self._saved is not None:
            os.environ[prog.OMIT_ENV] = self._saved


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


_CLAIMS: dict[str, list[Claim]] = {}


def _qualname(c: Claim) -> tuple[str, str]:
    func: Any = c.func
    try:
        qual = func.__qualname__
    except AttributeError:
        qual = repr(func)
    return (c.module, qual if isinstance(qual, str) else repr(func))


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
        _CLAIMS.setdefault(name, []).append(
            Claim(
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
        )
        return func

    return decorator


def claim_by_name(name: str) -> Claim:
    """Return the latest registered claim, raising KeyError when unknown."""
    try:
        return _CLAIMS[name][-1]
    except (KeyError, IndexError):
        raise KeyError(name) from None


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
    """Import the claims module and return every owned registration.

    Claims modules may be packages: a claim is owned when its function module is
    the configured module or starts with `<module>.`. Re-imports append
    duplicate records; selection dedupes them (SPEC §15.2).
    """
    if not module_name:
        raise ValueError("project.claims is not configured")
    module = prog.load_module(module_name, hub)
    prefix = module.__name__ + "."
    return [
        record
        for records in _CLAIMS.values()
        for record in records
        if record.module == module.__name__ or record.module.startswith(prefix)
    ]


def select_claims(
    records: list[Claim], backend: str | None, covers: str | None
) -> tuple[list[Claim], list[str]]:
    """Filter records, one claim per name, plus duplicate names (SPEC §15.2 step 1)."""
    groups: dict[str, list[Claim]] = {}
    for c in records:
        if backend is not None and c.backend != backend:
            continue
        if covers is not None and covers not in c.covers:
            continue
        groups.setdefault(c.name, []).append(c)
    claims = sorted((group[-1] for group in groups.values()), key=lambda c: c.name)
    dupes = sorted(
        name for name, group in groups.items() if len({_qualname(c) for c in group}) > 1
    )
    return claims, dupes


def first_message(exc: BaseException) -> str:
    """One line for a declaration failure (SPEC §15.2 step 2)."""
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        if errors:
            first = errors[0]
            loc = ".".join(str(part) for part in first.get("loc", ()))
            msg = str(first.get("msg", ""))
            return f"{loc}: {msg}" if loc else msg
    for line in str(exc).splitlines():
        if line.strip():
            return line.strip()
    return "invalid declaration"


def declaration_problem(c: Claim) -> str | None:
    """Check the declared method, scope and bound build a verified Finding (SPEC §15.2 step 2)."""
    try:
        Finding(
            id=c.name,
            claim=c.name,
            covers=list(c.covers),
            verdict=Verdict.VERIFIED,
            method=Method(c.method),
            scope=Scope(c.scope),
            bound=c.bound,
        )
    except ValueError as exc:
        return f"{c.name}: {first_message(exc)}"
    return None


def validate_claims(
    claims: list[Claim],
    active_ids: set[str],
    backends: dict[str, Backend],
    dupes: list[str] | None = None,
) -> list[str]:
    """Coverage, backend and declaration problems for every claim (SPEC §15.2 steps 1-2)."""
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
        problem = declaration_problem(c)
        if problem is not None:
            problems.append(problem)
    for name in dupes or []:
        problems.append(f"{name}: duplicate claim name")
    return problems


def kill_group(pid: int) -> None:
    """SIGKILL a process group, ignoring gone or foreign groups (SPEC §15.2 step 3)."""
    killpg = getattr(os, "killpg", None)
    if killpg is None:
        return
    try:
        killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _pump(stream: Any, chunks: list[bytes]) -> None:
    """Read a pipe to EOF in a background thread."""
    try:
        while True:
            data = stream.read(65536)
            if not data:
                break
            chunks.append(data)
    except Exception:
        pass


def run_claim(
    hub: Path, module_name: str, name: str, timeout: float, omit: str | None = None
) -> tuple[str, Any]:
    """Run one claim in a subprocess; return (status, payload) (SPEC §15.2 step 3).

    The runner's exit ends the claim: wait for the process itself, not for pipe
    EOF (a grandchild may hold the pipes). Output is pumped on background
    threads; after exit or timeout the group is SIGKILLed and output collected
    with a bounded wait. Status is timeout, ok (payload is a bool), finding
    (payload is a dict), other (payload is a type name), error (payload is a
    traceback string), exited (payload is the exit code) or unparsable.
    """
    env = dict(os.environ)
    if omit is None:
        env.pop(prog.OMIT_ENV, None)
    else:
        env[prog.OMIT_ENV] = omit
    proc = subprocess.Popen(
        [sys.executable, "-m", "helios.claims.runner", module_name, name],
        cwd=hub,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    out_chunks: list[bytes] = []
    err_chunks: list[bytes] = []
    readers = [
        threading.Thread(target=_pump, args=(proc.stdout, out_chunks), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, err_chunks), daemon=True),
    ]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            code = None
        kill_group(proc.pid)
        deadline = time.monotonic() + COLLECT_S
        for reader in readers:
            reader.join(timeout=max(0.0, deadline - time.monotonic()))
        if timed_out:
            try:
                proc.wait(timeout=COLLECT_S)
            except subprocess.TimeoutExpired:
                pass
            return ("timeout", None)
        return interpret_runner(code, b"".join(out_chunks).decode("utf-8", "replace"))
    finally:
        kill_group(proc.pid)
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass


def interpret_runner(returncode: int | None, stdout: str) -> tuple[str, Any]:
    """Map a runner exit code and stdout to a (status, payload) pair (SPEC §15.2 step 3)."""
    if returncode != 0:
        return ("exited", returncode)
    if len(stdout.splitlines()) != 1:
        return ("unparsable", stdout)
    try:
        message = json.loads(stdout.splitlines()[0])
    except json.JSONDecodeError:
        return ("unparsable", stdout)
    if not isinstance(message, dict):
        return ("unparsable", stdout)
    if "error" in message:
        return ("error", message["error"])
    if "finding" in message:
        return ("finding", message["finding"])
    if "other" in message:
        return ("other", message["other"])
    if "result" in message and isinstance(message["result"], bool):
        return ("ok", message["result"])
    return ("unparsable", stdout)


def verified_finding(c: Claim) -> Finding:
    """A verified finding carrying the declared fields (SPEC §15.2 step 3)."""
    return Finding(
        id=c.name,
        claim=c.name,
        covers=list(c.covers),
        verdict=Verdict.VERIFIED,
        method=Method(c.method),
        scope=Scope(c.scope),
        bound=c.bound,
        artifact=c.artifact,
    )


def inconclusive_finding(c: Claim, note: str, artifact: str | None = None) -> Finding:
    """An inconclusive finding keeping the declared method, scope and bound."""
    return Finding(
        id=c.name,
        claim=c.name,
        covers=list(c.covers),
        verdict=Verdict.INCONCLUSIVE,
        method=Method(c.method),
        scope=Scope(c.scope),
        bound=c.bound,
        artifact=artifact if artifact is not None else c.artifact,
        notes=note,
    )


def refuted_finding(c: Claim, backends: dict[str, Backend]) -> Finding:
    """A refuted finding, or inconclusive with the reason (SPEC §15.2 step 3)."""
    backend = backends.get(c.backend)
    if backend is None or Method.COUNTEREXAMPLE.value not in backend.methods:
        return inconclusive_finding(
            c, f"backend {c.backend!r} does not allow counterexample evidence"
        )
    try:
        return Finding(
            id=c.name,
            claim=c.name,
            covers=list(c.covers),
            verdict=Verdict.REFUTED,
            method=Method.COUNTEREXAMPLE,
            scope=Scope(c.scope),
            bound=c.bound,
            artifact=c.artifact,
        )
    except ValueError as exc:
        return _last_resort(c, f"refuted finding would not validate: {exc}")


def _last_resort(c: Claim, note: str) -> Finding:
    """An inconclusive finding that never raises.

    Step 2 rejects declarations that cannot validate, so every finding built in
    the check flow keeps the declared fields. This is only insurance for direct
    library misuse with an invalid declaration.
    """
    try:
        return inconclusive_finding(c, note)
    except ValueError:
        return Finding(
            id=c.name,
            claim=c.name,
            covers=list(c.covers),
            verdict=Verdict.INCONCLUSIVE,
            method=Method.REVIEW,
            scope=Scope.UNIVERSAL,
            notes=note,
        )


def timeout_note(timeout: float) -> str:
    """The timeout note of SPEC §15.2 step 3."""
    if isinstance(timeout, float) and timeout.is_integer():
        return f"timeout after {int(timeout)} s"
    return f"timeout after {timeout} s"


def returned_finding(c: Claim, payload: Any, backends: dict[str, Backend]) -> Finding:
    """Adopt a returned Finding with the claim's id, claim and covers (SPEC §15.2 step 3)."""
    try:
        finding = Finding.model_validate(payload)
    except ValueError as exc:
        return inconclusive_finding(c, f"claim returned an invalid finding: {exc}")
    finding.id = c.name
    finding.claim = c.name
    finding.covers = list(c.covers)
    backend = backends.get(c.backend)
    if backend is None:
        return inconclusive_finding(c, f"unknown backend {c.backend!r}")
    if finding.method.value not in backend.methods:
        return inconclusive_finding(
            c, f"backend {c.backend!r} does not allow method {finding.method.value!r}"
        )
    if finding.scope.value not in backend.scopes:
        return inconclusive_finding(
            c, f"backend {c.backend!r} does not allow scope {finding.scope.value!r}"
        )
    return finding


def check_claims(
    *,
    hub: Path,
    program: str | None,
    claims: str | None,
    backend: str | None = None,
    covers: str | None = None,
    timeout: float = 600,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Run `helios claims check` (SPEC §15.2); return the exit code."""
    stdout = out if out is not None else sys.stdout
    stderr = err if err is not None else sys.stderr
    with scrubbed_omit():
        return _check_claims(hub, program, claims, backend, covers, timeout, stdout, stderr)


def _check_claims(
    hub: Path,
    program: str | None,
    claims: str | None,
    backend: str | None,
    covers: str | None,
    timeout: float,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Run `helios claims check` (SPEC §15.2); return the exit code."""
    backends = load_backends(hub)
    _, registry = prog.load_registry(hub, program)
    if not claims:
        raise ValueError("project.claims is not configured")
    selected, dupes = select_claims(load_claims(hub, claims), backend, covers)
    active_ids = {b.id for b in registry.active()}
    problems = validate_claims(selected, active_ids, backends, dupes)
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
        return refuted_finding(c, backends)
    if status == "finding":
        return returned_finding(c, payload, backends)
    if status == "error":
        path = hub / ".helios" / "claims" / f"{c.name}.traceback.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(payload))
        artifact = str(path.relative_to(hub))
        return inconclusive_finding(c, f"claim raised; traceback saved to {artifact}", artifact)
    if status == "timeout":
        return inconclusive_finding(c, timeout_note(timeout))
    if status == "other":
        return inconclusive_finding(c, f"claim returned {payload}")
    if status == "exited":
        return inconclusive_finding(c, f"runner exited {payload}")
    return inconclusive_finding(c, "unparsable runner output")


def attack_claim(
    *,
    hub: Path,
    program: str | None,
    claims: str | None,
    name: str,
    timeout: float = 600,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Run `helios claims attack <claim>` (SPEC §15.2); return the exit code."""
    stdout = out if out is not None else sys.stdout
    stderr = err if err is not None else sys.stderr
    with scrubbed_omit():
        return _attack_claim(hub, program, claims, name, timeout, stdout, stderr)


def _attack_claim(
    hub: Path,
    program: str | None,
    claims: str | None,
    name: str,
    timeout: float,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Run `helios claims attack <claim>` (SPEC §15.2); return the exit code."""
    _, registry = prog.load_registry(hub, program)
    if not claims:
        raise ValueError("project.claims is not configured")
    selected, dupes = select_claims(load_claims(hub, claims), None, None)
    if dupes:
        for name in dupes:
            print(f"{name}: duplicate claim name", file=stderr)
        return 2
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
