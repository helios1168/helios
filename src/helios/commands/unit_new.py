"""``helios unit new`` (SPEC §10.2). Argument parsing only; logic is helios.units."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from helios import beads, config, units

NAME = "unit new"
HELP = "Create the bead chain and unit file for a new unit."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Define the ``unit new`` arguments (SPEC §2.3)."""
    parser.add_argument("unit", help="Unit id (SPEC §10.2 step 1).")
    parser.add_argument("title", help="Unit title.")
    parser.add_argument(
        "--stages",
        required=True,
        help="Comma-separated stage ids in SPEC §3 row order.",
    )
    parser.add_argument(
        "--files",
        default=None,
        help="Comma-separated globs; required when impl or validate is present.",
    )
    parser.add_argument(
        "--test",
        default=None,
        help="Bead test command; required when impl or validate is present.",
    )


def run(args: argparse.Namespace) -> int:
    """Create the unit chain and print the step 6 table."""
    try:
        cfg = config.load(Path.cwd())
    except (ValueError, TypeError) as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
    try:
        rows = units.create_unit(
            beads=beads.Beads(cfg.hub),
            config=cfg,
            unit=args.unit,
            title=args.title,
            stages=args.stages,
            files=args.files,
            test=args.test,
        )
    except units.UnitNewError as exc:
        print(exc, file=sys.stderr)
        return 2
    sys.stdout.write(units.format_table(rows))
    return 0
