from __future__ import annotations

import sys
from pathlib import Path

from helios import config, sessions

NAME = "stop"
HELP = "Stop a running attempt."


def add_arguments(parser) -> None:
    parser.add_argument("bead")


def run(args) -> int:
    try:
        cfg = config.load(Path.cwd())
        sessions.stop(cfg.hub, cfg.project.runs, args.bead)
    except (OSError, TypeError, ValueError) as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
    return 0
