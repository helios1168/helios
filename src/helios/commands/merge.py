"""The ``helios merge`` command (SPEC section 12)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from helios.beads import BeadNotFound, Beads
from helios.config import load
from helios.merge import MergeError, merge_bead

NAME = "merge"
HELP = "Integrate an implementation bead."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("bead")
    parser.add_argument("--dry-run", action="store_true")


def _print_error(message: str) -> None:
    """Print every line of `message` to stderr, each prefixed ``helios: `` (SPEC 12 item 4)."""
    for line in message.splitlines() or [message]:
        print(f"helios: {line}", file=sys.stderr)


def run(args: argparse.Namespace) -> int:
    try:
        config = load(Path.cwd())
    except (ValueError, TypeError, RecursionError) as exc:
        _print_error(str(exc))
        return 2
    except Exception as exc:
        _print_error(str(exc) or type(exc).__name__)
        return 1
    try:
        code, message = merge_bead(
            config.hub,
            args.bead,
            project=config.project,
            beads=Beads(config.hub),
            dry_run=args.dry_run,
        )
    except MergeError as exc:
        _print_error(str(exc))
        return exc.code
    except BeadNotFound:
        _print_error(f"no bead {args.bead}")
        return 2
    except Exception as exc:
        _print_error(str(exc) or type(exc).__name__)
        return 1
    print(message)
    return code
