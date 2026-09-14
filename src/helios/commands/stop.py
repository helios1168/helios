from __future__ import annotations

import sys
from pathlib import Path

from helios import config, sessions

NAME = "stop"
HELP = "Stop a running attempt."


def add_arguments(parser) -> None:
    parser.add_argument("bead")


def run(args) -> int:
    cfg = config.load(Path.cwd())
    try:
        sessions.stop(cfg.hub, cfg.project.runs, args.bead)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0
