from __future__ import annotations

import sys
from pathlib import Path

from helios import config, messages, sessions

NAME = "say"
HELP = "Queue a message for an attempt."


def add_arguments(parser) -> None:
    parser.add_argument("bead")
    parser.add_argument("text")
    parser.add_argument("--kind", choices=("steer", "answer"), default="steer")


def run(args) -> int:
    if not sessions.BEAD_ID_RE.fullmatch(args.bead):
        print(f"helios: invalid bead id {args.bead}", file=sys.stderr)
        return 2
    try:
        cfg = config.load(Path.cwd())
    except (OSError, TypeError, ValueError, RecursionError) as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
    runs = cfg.hub / cfg.project.runs
    if sessions.latest(runs, args.bead) is None:
        print(f"helios: no runs directory for {args.bead}", file=sys.stderr)
        return 2
    try:
        msg_id, queued = messages.say(cfg.hub, cfg.project.runs, args.bead,
                                      args.text, kind=args.kind)
    except Exception as exc:
        print(f"helios: {str(exc) or type(exc).__name__}", file=sys.stderr)
        return 1
    if queued:
        print(f"helios: queued {msg_id}", file=sys.stderr)
        return 3
    print(msg_id)
    return 0
