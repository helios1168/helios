"""`helios claims attack <claim>` (SPEC §15.2)."""

from __future__ import annotations

import argparse

NAME = "claims attack"
HELP = "Rerun one claim with each covered block omitted."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("claim", help="Name of the claim to attack.")


def run(args: argparse.Namespace) -> int:
    import sys
    from pathlib import Path

    from helios import claims as claims_lib
    from helios import config as cfg

    try:
        config = cfg.load(Path.cwd())
        return claims_lib.attack_claim(
            hub=config.hub,
            program=config.project.program,
            claims=config.project.claims,
            name=args.claim,
        )
    except (ValueError, TypeError) as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
