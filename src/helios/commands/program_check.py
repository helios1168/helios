"""`helios program check` (SPEC §15.3)."""

from __future__ import annotations

import argparse

NAME = "program check"
HELP = "Check that constraint and objective builds record exactly their ids."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    pass


def run(args: argparse.Namespace) -> int:
    import sys
    from pathlib import Path

    from helios import config as cfg
    from helios import program as prog

    try:
        config = cfg.load(Path.cwd())
        module, registry = prog.load_registry(config.hub, config.project.program)
        missing, unexpected = prog.check_registry(registry, getattr(module, "DATA", None))
        if missing:
            print(f"missing: {', '.join(missing)}")
        if unexpected:
            print(f"unexpected: {', '.join(unexpected)}")
        return 5 if missing or unexpected else 0
    except (ValueError, TypeError) as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
