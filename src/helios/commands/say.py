from __future__ import annotations

import sys
from pathlib import Path

from helios import config, messages

NAME = "say"
HELP = "Queue a message for an attempt."


def add_arguments(parser) -> None:
    parser.add_argument("bead")
    parser.add_argument("text")
    parser.add_argument("--kind", choices=("steer", "answer"), default="steer")


def run(args) -> int:
    cfg = config.load(Path.cwd())
    runs = cfg.hub / cfg.project.runs
    if sessions.latest(runs, args.bead) is None:
        print(f"no runs directory for {args.bead}", file=sys.stderr)
        return 2
    try:
        msg_id, queued = messages.say(cfg.hub, cfg.project.runs, args.bead,
                                      args.text, kind=args.kind)
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if queued:
        print(f"queued {msg_id}", file=sys.stderr)
        return 3
    print(msg_id)
    return 0
