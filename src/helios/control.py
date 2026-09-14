"""Candidate selection and stage control (SPEC sections 11 and 3)."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable

from helios.beads import Bead, BeadsLike
from helios.envelope import Envelope, ExecutionStatus, Verdict, WorkStatus, overall_verdict
from helios.stages import STAGES, VERIFY_STAGES


class ControlError(ValueError):
    """A control setting or unit request is invalid."""


def candidates(beads: BeadsLike, unit: str | None = None) -> list[Bead]:
    """Return open, staged ready beads in bd order (SPEC section 11)."""
    wanted = f"unit:{unit}" if unit else None
    return [
        bead
        for bead in beads.ready()
        if bead.status == "open"
        and bead.kind in STAGES
        and f"kind:{bead.kind}" in bead.labels
        and (wanted is None or wanted in bead.labels)
    ]


def envelope_line(envelope: Envelope) -> str:
    """Render the exact one-line envelope summary used by ``next``.

    The verdict column is ``overall_verdict(report.findings)`` (SPEC section 4.2), never a
    field read off the report directly, since ``AgentReport`` carries no ``verdict`` of its
    own.
    """
    report = envelope.report
    verdict = overall_verdict(report.findings) if report is not None else None
    return "\t".join(
        [
            envelope.attempt_id,
            str(envelope.execution_status),
            str(report.status) if report is not None else "-",
            str(verdict) if verdict is not None else "-",
            (report.summary if report is not None and report.summary else "-"),
        ]
    )


def _execute(
    bead: Bead,
    run: Callable[[Bead], int],
    read_envelope: Callable[[Bead], Envelope | None],
    latest_attempt: Callable[[Bead], int | None] | None = None,
) -> tuple[int, Envelope | None]:
    """Run ``bead`` and read its envelope, converting either callable's exception.

    A raising ``run`` or ``read_envelope`` is an execution failure (SPEC section 7.1 exit
    code 4), reported as ``execution failure for <bead>: <exception type>: <message>``.

    When ``latest_attempt`` is given, its value before and after ``run`` is compared
    (Decided): a run that makes no new attempt is its own execution failure, reason
    ``execution failure for <bead>: no new attempt``, carrying the run's own exit code
    so the caller can use it (``execution_failure_exit``). ``latest_attempt`` is
    optional so existing callers that do not track attempts keep working unchanged.
    """
    before = latest_attempt(bead) if latest_attempt is not None else None
    try:
        code = int(run(bead))
    except Exception as exc:
        raise ExecutionFailure(f"execution failure for {bead.id}: {type(exc).__name__}: {exc}") from exc
    if latest_attempt is not None and latest_attempt(bead) == before:
        raise ExecutionFailure(f"execution failure for {bead.id}: no new attempt", code=code)
    try:
        envelope = read_envelope(bead)
    except Exception as exc:
        raise ExecutionFailure(f"execution failure for {bead.id}: {type(exc).__name__}: {exc}") from exc
    return code, envelope


class ExecutionFailure(Exception):
    """A run or its envelope could not be produced; carries the SPEC section 7.1 reason.

    ``code`` is the run's own exit code when it is known (a raised exception leaves it
    ``None``). ``execution_failure_exit`` turns it into the exit code to use (Decided).
    """

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def execution_failure_exit(code: int | None) -> int:
    """SPEC section 7.1 exit code 4, or the run's own code when it is nonzero (Decided)."""
    return code if code else 4


def next_bead(
    beads: BeadsLike,
    *,
    unit: str | None,
    stop_at: tuple[str, ...],
    run: Callable[[Bead], int],
    read_envelope: Callable[[Bead], Envelope | None],
    latest_attempt: Callable[[Bead], int | None] | None = None,
) -> int:
    """Run the first candidate not covered by ``stop_at`` (SPEC section 11)."""
    bead = next((b for b in candidates(beads, unit) if b.kind not in stop_at), None)
    if bead is None:
        print("helios: no ready bead", file=sys.stderr)
        return 3
    try:
        code, envelope = _execute(bead, run, read_envelope, latest_attempt)
    except ExecutionFailure as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return execution_failure_exit(exc.code)
    if envelope is not None:
        print(envelope_line(envelope))
    return code


@dataclass(frozen=True)
class UnitResult:
    code: int
    reason: str


def validate_until(beads: Any, unit: str, until: str | None) -> str | None:
    """Validate control values and return the effective until stage."""
    if until is not None and until not in STAGES:
        raise ControlError(f"unknown until stage {until}")
    if until is not None and not any(
        bead.kind == until and f"kind:{until}" in bead.labels
        for bead in beads.list(labels=[f"unit:{unit}"])
    ):
        raise ControlError(f"until stage {until} absent from unit {unit}")
    return until


def unit_run(
    beads: BeadsLike,
    *,
    unit: str,
    default: str,
    configured_until: str,
    stop_at: tuple[str, ...],
    until: str | None,
    run: Callable[[Bead], int],
    read_envelope: Callable[[Bead], Envelope | None],
    latest_attempt: Callable[[Bead], int | None] | None = None,
) -> UnitResult:
    """Run a unit until a SPEC section 11 stopping condition.

    After a run, the stop checks apply in this fixed order (Decided, binding):
    1. execution failure: no envelope, or ``execution_status`` is not ``completed``.
    2. ``impl`` and ``validate`` kinds: report status other than ``done``.
    3. verify kinds (``helios.stages.VERIFY_STAGES``): verdict other than ``verified``,
       judged on the report's findings, never on its status.
    4. any other nonzero run code (a failed helios check).
    5. the bead's kind is the ``until`` stage.
    Kinds that are neither ``impl``, ``validate`` nor a verify kind skip checks 2 and 3.
    """
    if default not in {"manual", "until", "auto"}:
        raise ControlError(f"unknown control.default {default}")
    effective_until = validate_until(beads, unit, until)
    if effective_until is None:
        if default == "manual":
            raise ControlError("manual control requires --until")
        if default == "until":
            effective_until = configured_until
            validate_until(beads, unit, effective_until)

    seen: set[str] = set()
    last_code = 3
    while True:
        available = candidates(beads, unit)
        if not available:
            reason = "no ready bead"
            print(f"stopped: {reason}")
            return UnitResult(3, reason)
        bead = available[0]
        if bead.id in seen:
            reason = f"bead {bead.id} still ready after its run"
            print(f"stopped: {reason}")
            return UnitResult(last_code, reason)
        if bead.kind in stop_at:
            reason = f"stop_at stage {bead.kind}"
            print(f"stopped: {reason}")
            return UnitResult(3, reason)
        seen.add(bead.id)
        try:
            code, envelope = _execute(bead, run, read_envelope, latest_attempt)
        except ExecutionFailure as exc:
            reason = str(exc)
            print(f"stopped: {reason}")
            return UnitResult(execution_failure_exit(exc.code), reason)
        last_code = code
        if envelope is not None:
            print(envelope_line(envelope))

        if envelope is None or envelope.execution_status != ExecutionStatus.COMPLETED:
            detail = envelope.execution_status if envelope is not None else "missing envelope"
            reason = f"execution failure for {bead.id}: {detail}"
            print(f"stopped: {reason}")
            return UnitResult(execution_failure_exit(code), reason)

        report = envelope.report  # completed always carries a report (Envelope's own rule)
        assert report is not None
        if bead.kind in ("impl", "validate") and report.status != WorkStatus.DONE:
            reason = f"bead {bead.id} report status {report.status}"
            print(f"stopped: {reason}")
            return UnitResult(code, reason)
        if bead.kind in VERIFY_STAGES:
            verdict = overall_verdict(report.findings)
            if verdict != Verdict.VERIFIED:
                reason = f"bead {bead.id} verdict {verdict if verdict is not None else '-'}"
                print(f"stopped: {reason}")
                return UnitResult(code, reason)
        if code != 0:
            reason = f"bead {bead.id} run exited {code}"
            print(f"stopped: {reason}")
            return UnitResult(code, reason)
        if effective_until == bead.kind:
            print("stopped: until stage reached")
            return UnitResult(0, "until stage reached")
