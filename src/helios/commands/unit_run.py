from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from helios import control
from helios.attempt import existing_attempts, runs_dir
from helios.beads import Beads, Bead
from helios.config import load
from helios.envelope import Envelope

NAME = "unit run"
HELP = "run a unit"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("unit")
    parser.add_argument("--until")


def run(args: argparse.Namespace) -> int:
    try:
        config = load(Path.cwd())
    except (ValueError, TypeError, RecursionError) as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
    beads = Beads(config.hub)
    try:
        result = control.unit_run(
            beads,
            unit=args.unit,
            default=config.control.default,
            configured_until=config.control.until,
            stop_at=config.control.stop_at,
            until=args.until,
            run=run_bead,
            read_envelope=read_envelope,
            latest_attempt=latest_attempt,
        )
    except control.ControlError as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
    return result.code


def run_bead(bead: Bead) -> int:
    """Dispatch through ``helios.run`` (SPEC section 7.1).

    Imported lazily: hel-6ks, which adds that module, is not on main yet. A missing
    module surfaces as a normal execution failure (control.py).
    """
    config = load(Path.cwd())
    beads = Beads(config.hub)
    run_many = importlib.import_module("helios.run").run_many
    return int(run_many([bead.id], hub=config.hub, beads=beads, config=config))


def read_envelope(bead: Bead) -> Envelope | None:
    """The envelope of the bead's highest attempt, or None (SPEC section 8.3)."""
    config = load(Path.cwd())
    bead_runs = runs_dir(config.hub, config.project.runs, bead.id)
    numbers = existing_attempts(bead_runs)
    if not numbers:
        return None
    path = bead_runs / f"attempt-{numbers[-1]}" / "envelope.json"
    if not path.is_file():
        return None
    return Envelope.model_validate_json(path.read_text())


def latest_attempt(bead: Bead) -> int | None:
    """The bead's highest existing attempt number, or None (SPEC section 8.3).

    Compared before and after ``run_bead`` (control.py's ``_execute``) to catch a run
    that makes no new attempt (Decided).
    """
    config = load(Path.cwd())
    bead_runs = runs_dir(config.hub, config.project.runs, bead.id)
    numbers = existing_attempts(bead_runs)
    return numbers[-1] if numbers else None
