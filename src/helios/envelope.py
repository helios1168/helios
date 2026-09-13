"""Result contracts (SPEC §4).

Two shapes, kept apart on purpose:

- AgentReport is what the agent writes as its last act. It states work status and scientific
  findings. The agent cannot state its own execution status.
- Envelope is what helios writes after the native process ends. It binds the report to its
  inputs (hashes, commits, session, attempt) and records execution facts the agent cannot know.

Run `uv run python -m helios.envelope` to regenerate schemas/*.schema.json. A test checks the
committed files match these models.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1"
HarnessName = Literal["claude", "codex", "opencode", "agy", "fake"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class WorkStatus(StrEnum):
    """Agent-declared state of the work (SPEC §4.1)."""

    DONE = "done"  # accept text met and the bead test passes
    PARTIAL = "partial"  # progress made, work remains; resume within the same contract
    NEEDS_INPUT = "needs_input"  # blocked on a question for the orchestrator; see `question`
    NEEDS_REVIEW = "needs_review"  # wants the orchestrator to review before continuing
    BLOCKED = "blocked"  # cannot proceed; reason in `summary`


class Verdict(StrEnum):
    VERIFIED = "verified"
    REFUTED = "refuted"  # needs a concrete counterexample or a reproduced mismatch
    INCONCLUSIVE = "inconclusive"
    UNSUPPORTED = "unsupported"  # the method or backend cannot decide this claim


class Method(StrEnum):
    """How the evidence was produced. The method alone never implies a verdict."""

    PROOF = "proof"  # written or symbolic derivation checked by the verifier
    MACHINE_CHECKED_PROOF = "machine_checked_proof"  # proof assistant kernel, standard axioms
    EXHAUSTIVE_FINITE_CHECK = "exhaustive_finite_check"  # every case in a stated finite domain
    NUMERICAL_CERTIFICATE = "numerical_certificate"  # enclosure or tolerance stated in `bound`
    CERTIFIED_SOLVE = "certified_solve"  # solver certificate checked by an independent checker
    EMPIRICAL_SUPPORT = "empirical_support"  # tests, sampling, property search
    COUNTEREXAMPLE = "counterexample"  # concrete input that breaks the claim
    REVIEW = "review"  # reading and judgment only


class Scope(StrEnum):
    UNIVERSAL = "universal"  # all instances the claim quantifies over
    BOUNDED = "bounded"  # a stated finite region, recorded in `bound`
    INSTANCE = "instance"  # named instances only, recorded in `bound`


class Checker(_Model):
    """A command that re-checks the artifact, and what it returned."""

    command: str
    exit_code: int | None = None
    output_path: str | None = None


class Finding(_Model):
    """One claim with its evidence (SPEC §4.2)."""

    id: str
    claim: str
    covers: list[str] = Field(default_factory=list, description="Block or requirement ids.")
    verdict: Verdict
    method: Method
    scope: Scope
    bound: dict[str, str] | None = Field(
        default=None, description="Domain, sizes, tolerance or instance names for the scope."
    )
    assumptions: list[str] = Field(default_factory=list)
    artifact: str | None = Field(default=None, description="Repo-relative path or exact command.")
    checker: Checker | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _scope_rules(self) -> Finding:
        if self.scope in (Scope.BOUNDED, Scope.INSTANCE) and not self.bound:
            raise ValueError(f"finding {self.id}: scope {self.scope.value} requires `bound`")
        if self.method is Method.EXHAUSTIVE_FINITE_CHECK and self.scope is Scope.UNIVERSAL:
            raise ValueError(f"finding {self.id}: an exhaustive finite check is not universal")
        if self.method is Method.EMPIRICAL_SUPPORT and self.verdict is Verdict.VERIFIED:
            if self.scope is Scope.UNIVERSAL:
                raise ValueError(
                    f"finding {self.id}: empirical support cannot verify a universal claim"
                )
        if self.verdict is Verdict.REFUTED and self.method is not Method.COUNTEREXAMPLE:
            if not self.artifact:
                raise ValueError(f"finding {self.id}: REFUTED needs an artifact")
        return self


class TestRun(_Model):
    __test__ = False  # not a pytest class

    command: str
    passed: int
    failed: int
    exit_code: int | None = None


class AgentReport(_Model):
    """Written by the agent to the report path named in its prompt (SPEC §4.1)."""

    schema_version: Literal["1"] = SCHEMA_VERSION
    status: WorkStatus
    summary: str = Field(description="One line; becomes the commit message tail and bead note.")
    files_changed: list[str] = Field(default_factory=list)
    tests: TestRun | None = None
    findings: list[Finding] = Field(default_factory=list)
    question: str | None = Field(default=None, description="Required when status is needs_input.")
    learned: list[str] = Field(default_factory=list)
    missing_context: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _question_rule(self) -> AgentReport:
        if self.status is WorkStatus.NEEDS_INPUT and not self.question:
            raise ValueError("status needs_input requires `question`")
        return self


class ExecutionStatus(StrEnum):
    """Process facts recorded by helios, never by the agent (SPEC §4.4)."""

    COMPLETED = "completed"  # native turn completed and a valid report was captured
    INTERRUPTED = "interrupted"  # stopped by the user or `helios stop`
    TIMED_OUT = "timed_out"
    CRASHED = "crashed"  # nonzero exit or native error without a valid report
    MISSING_OUTPUT = "missing_output"  # native completion but no report
    INVALID_OUTPUT = "invalid_output"  # report present but fails the schema
    LAUNCH_FAILED = "launch_failed"  # binary missing, preflight passed but exec failed


class Check(_Model):
    """A check helios ran itself after the attempt (bead test, ownership, typecheck)."""

    name: str
    passed: bool
    command: str | None = None
    exit_code: int | None = None
    detail: str = ""
    log_path: str | None = None


class Envelope(_Model):
    """Written by helios to .helios/runs/<bead>/attempt-<n>/envelope.json (SPEC §4.3)."""

    schema_version: Literal["1"] = SCHEMA_VERSION
    task_id: str = Field(description="Bead id.")
    attempt: int = Field(ge=1)
    attempt_id: str = Field(description="'<bead>#<attempt>'.")
    kind: str = Field(description="Stage id of the bead, SPEC §3.")
    harness: HarnessName
    model: str | None = None
    session_id: str | None = None
    started_at: str
    finished_at: str | None = None
    base_commit: str
    output_commit: str | None = None
    input_hashes: dict[str, str] = Field(
        description="sha256 per input: prompt, bead, each doc and memory, unit file."
    )
    execution_status: ExecutionStatus
    exit_code: int | None = None
    report: AgentReport | None = None
    report_error: str | None = None
    checks: list[Check] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    steered: list[str] = Field(default_factory=list, description="Extra user turns, SPEC §9.")
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistency(self) -> Envelope:
        if self.attempt_id != f"{self.task_id}#{self.attempt}":
            raise ValueError("attempt_id must be '<task_id>#<attempt>'")
        if self.execution_status is ExecutionStatus.COMPLETED and self.report is None:
            raise ValueError("execution_status completed requires a report")
        return self


_VERDICT_ORDER = [Verdict.REFUTED, Verdict.INCONCLUSIVE, Verdict.UNSUPPORTED, Verdict.VERIFIED]


def overall_verdict(findings: list[Finding]) -> Verdict | None:
    """REFUTED if any is, else INCONCLUSIVE, else UNSUPPORTED, else VERIFIED; None if empty."""
    present = {f.verdict for f in findings}
    return next((v for v in _VERDICT_ORDER if v in present), None)


SCHEMAS: dict[str, type[BaseModel]] = {
    "agent-report": AgentReport,
    "envelope": Envelope,
}


def schema_text(name: str) -> str:
    return json.dumps(SCHEMAS[name].model_json_schema(), indent=2, sort_keys=True) + "\n"


def write_schemas(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    out = []
    for name in SCHEMAS:
        path = directory / f"{name}.schema.json"
        path.write_text(schema_text(name))
        out.append(path)
    return out


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    for p in write_schemas(root / "schemas"):
        print(p.relative_to(root))
