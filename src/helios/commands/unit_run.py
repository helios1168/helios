from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from helios import control
from helios.beads import Beads, Bead
from helios.config import load

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
        result = control.unit_run(beads, unit=args.unit, default=config.control.default, configured_until=config.control.until, stop_at=config.control.stop_at, until=args.until, run=run_bead, read_envelope=read_envelope)
    except control.ControlError as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
    return result.code


def run_bead(bead: Bead) -> int:
    raise NotImplementedError("helios run is supplied by the run command")


def read_envelope(bead: Bead) -> dict[str, Any]:
    raise NotImplementedError("helios run is supplied by the run command")
