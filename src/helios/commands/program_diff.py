"""`helios program diff` (SPEC §15.3)."""

from __future__ import annotations

import argparse

NAME = "program diff"
HELP = "Diff active program block ids between two git revisions."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("rev1", help="Older git revision.")
    parser.add_argument("rev2", help="Newer git revision.")


def run(args: argparse.Namespace) -> int:
    import sys
    from pathlib import Path

    from helios import config as cfg
    from helios import program as prog

    try:
        config = cfg.load(Path.cwd())
        if not config.project.program:
            raise ValueError("project.program is not configured")
        old = prog.active_ids_at_revision(config.hub, config.project.program, args.rev1)
        new = prog.active_ids_at_revision(config.hub, config.project.program, args.rev2)
        for line in prog.diff_ids(old, new):
            print(line)
        return 0
    except (ValueError, TypeError) as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
