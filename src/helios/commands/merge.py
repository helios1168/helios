"""The ``helios merge`` command (SPEC section 12)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from helios.beads import Beads
from helios.config import load
from helios.merge import MergeError, merge_bead

NAME = "merge"
HELP = "Integrate an implementation bead."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("bead")
    parser.add_argument("--dry-run", action="store_true")


def run(args: argparse.Namespace) -> int:
    try:
        config = load(Path.cwd())
    except (ValueError, TypeError, RecursionError) as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
    try:
        code, message = merge_bead(
            config.hub,
            args.bead,
            project=config.project,
            beads=Beads(config.hub),
            dry_run=args.dry_run,
        )
    except MergeError as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return exc.code
    print(message)
    return code
