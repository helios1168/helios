"""Candidate selection and stage control (SPEC sections 11 and 3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from helios.beads import Bead, BeadsLike
from helios.envelope import Envelope
from helios.stages import STAGES


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


def envelope_line(envelope: Envelope | dict[str, Any]) -> str:
    """Render the exact one-line envelope summary used by ``next``."""
    data = envelope if isinstance(envelope, dict) else envelope.model_dump(mode="json")
    report = data.get("report") or {}
    return "\t".join(
        [
            str(data.get("attempt_id", "")),
            str(data.get("execution_status", "")),
            str(report.get("status") or "-"),
            str(report.get("verdict") or "-"),
            str(report.get("summary") or "-"),
        ]
    )


def _run_one(
    bead: Bead,
    run: Callable[[Bead], int],
    read_envelope: Callable[[Bead], Envelope | dict[str, Any]],
) -> tuple[int, Envelope | dict[str, Any], str]:
    code = int(run(bead))
    envelope = read_envelope(bead)
    return code, envelope, envelope_line(envelope)


def next_bead(
    beads: BeadsLike,
    *,
    unit: str | None,
    stop_at: tuple[str, ...],
    run: Callable[[Bead], int],
    read_envelope: Callable[[Bead], Envelope | dict[str, Any]],
) -> int:
    """Run the first candidate not covered by ``stop_at`` (SPEC section 11)."""
    bead = next((b for b in candidates(beads, unit) if b.kind not in stop_at), None)
    if bead is None:
        print("helios: no ready bead", file=__import__("sys").stderr)
        return 3
    code, _envelope, line = _run_one(bead, run, read_envelope)
    print(line)
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
    read_envelope: Callable[[Bead], Envelope | dict[str, Any]],
) -> UnitResult:
    """Run a unit until a SPEC section 11 stopping condition."""
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
            code, envelope, line = _run_one(bead, run, read_envelope)
        except Exception as exc:
            reason = f"execution failure for {bead.id}: {exc}"
            print(f"stopped: {reason}")
            return UnitResult(1, reason)
        print(line)
        last_code = code
        if code != 0:
            reason = f"execution failure for {bead.id}"
            print(f"stopped: {reason}")
            return UnitResult(code, reason)
        data = envelope if isinstance(envelope, dict) else envelope.model_dump(mode="json")
        report = data.get("report") or {}
        if report.get("status") != "done":
            reason = f"bead {bead.id} report status {report.get('status') or '-'}"
            print(f"stopped: {reason}")
            return UnitResult(last_code, reason)
        if report.get("verdict") not in (None, "verified") and bead.kind.startswith("verify"):
            reason = f"bead {bead.id} verdict {report.get('verdict')}"
            print(f"stopped: {reason}")
            return UnitResult(last_code, reason)
        if effective_until == bead.kind:
            print("stopped: until stage reached")
            return UnitResult(0, "until stage reached")
        if default == "auto" and until is None and bead.kind in stop_at:
            reason = f"stop_at stage {bead.kind}"
            print(f"stopped: {reason}")
            return UnitResult(3, reason)
