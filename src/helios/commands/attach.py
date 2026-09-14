from __future__ import annotations

from pathlib import Path

from helios import config, harness, sessions

NAME = "attach"
HELP = "Attach to an attempt."


def add_arguments(parser) -> None:
    parser.add_argument("bead")


def run(args) -> int:
    cfg = config.load(Path.cwd())
    try:
        lookup = getattr(harness, "get", None)
        if lookup is None:
            lookup = lambda name: None
        sessions.attach(cfg.hub, cfg.project.runs, args.bead, harness_lookup=lookup)
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 2
    return 0
