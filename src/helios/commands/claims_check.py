"""`helios claims check` (SPEC §15.2)."""

from __future__ import annotations

import argparse

NAME = "claims check"
HELP = "Run the project's claims and print one JSON finding per line."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--backend", default=None, help="Only run claims with this backend.")
    parser.add_argument("--covers", default=None, help="Only run claims covering this id.")
    parser.add_argument("--timeout", type=float, default=600.0, help="Per-claim timeout in s.")


def run(args: argparse.Namespace) -> int:
    import sys
    from pathlib import Path

    from helios import claims as claims_lib
    from helios import config as cfg

    try:
        config = cfg.load(Path.cwd())
        return claims_lib.check_claims(
            hub=config.hub,
            program=config.project.program,
            claims=config.project.claims,
            backend=args.backend,
            covers=args.covers,
            timeout=args.timeout,
        )
    except (ValueError, TypeError) as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
