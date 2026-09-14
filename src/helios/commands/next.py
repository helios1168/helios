from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from helios import control
from helios.attempt import attempt_dir, existing_attempts, read_state, runs_dir
from helios.beads import Beads, Bead
from helios.config import load
from helios.envelope import Envelope

NAME = "next"
HELP = "run the next ready bead"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("unit", nargs="?")


def run(args: argparse.Namespace) -> int:
    try:
        config = load(Path.cwd())
    except (ValueError, TypeError, RecursionError) as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
    beads = Beads(config.hub)
    return control.next_bead(
        beads,
        unit=args.unit,
        stop_at=config.control.stop_at,
        run=run_bead,
        read_envelope=read_envelope,
        attempt_state=attempt_state,
    )


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


def attempt_state(bead: Bead) -> control.AttemptState:
    """The bead's highest attempt number (0 for none) and whether it was finalized
    (SPEC sections 8.2/8.3). Snapshotted before and after ``run_bead`` (control.py's
    ``_execute``) so a genuinely new attempt can be told apart from a SPEC section 8.4
    in-place recovery of a not-yet-finalized one (Decided, revised).
    """
    config = load(Path.cwd())
    bead_runs = runs_dir(config.hub, config.project.runs, bead.id)
    numbers = existing_attempts(bead_runs)
    if not numbers:
        return control.AttemptState(number=0, finalized=False)
    n = numbers[-1]
    state = read_state(attempt_dir(config.hub, config.project.runs, bead.id, n))
    return control.AttemptState(number=n, finalized=state.get("state") == "finalized")
