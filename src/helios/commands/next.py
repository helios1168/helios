from __future__ import annotations

import argparse
from pathlib import Path

from helios import control
from helios.beads import Beads, Bead
from helios.config import load

NAME = "next"
HELP = "run the next ready bead"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("unit", nargs="?")


def run(args: argparse.Namespace) -> int:
    config = load(Path.cwd())
    beads = Beads(config.hub)
    return control.next_bead(beads, unit=args.unit, stop_at=config.control.stop_at, run=run_bead, read_envelope=read_envelope)


def run_bead(bead: Bead) -> int:
    return 0


def read_envelope(bead: Bead):
    raise NotImplementedError
