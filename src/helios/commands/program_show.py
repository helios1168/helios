"""`helios program show` (SPEC §15.3)."""

from __future__ import annotations

import argparse

NAME = "program show"
HELP = "Print the program registry as deterministic markdown."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", default=None, help="Write the file instead of stdout.")


def run(args: argparse.Namespace) -> int:
    import sys
    from pathlib import Path

    from helios import config as cfg
    from helios import program as prog

    try:
        config = cfg.load(Path.cwd())
        _, registry = prog.load_registry(config.hub, config.project.program)
        text = prog.show_text(registry)
        if args.output is not None:
            Path(args.output).write_text(text)
        else:
            sys.stdout.write(text)
        return 0
    except (ValueError, TypeError) as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
