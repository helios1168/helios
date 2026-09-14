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
    try:
        cfg = config.load(Path.cwd())
        runs = cfg.hub / cfg.project.runs
        if sessions.latest(runs, args.bead) is None:
            print(f"helios: no runs directory for {args.bead}", file=sys.stderr)
            return 2
        msg_id, queued = messages.say(cfg.hub, cfg.project.runs, args.bead,
                                      args.text, kind=args.kind)
    except RuntimeError as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError) as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
    if queued:
        print(f"helios: queued {msg_id}", file=sys.stderr)
        return 3
    print(msg_id)
    return 0
