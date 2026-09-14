from __future__ import annotations

import importlib
from pathlib import Path

from helios import config, sessions

NAME = "attach"
HELP = "Attach to an attempt."


def add_arguments(parser) -> None:
    parser.add_argument("bead")


def run(args) -> int:
    try:
        cfg = config.load(Path.cwd())
        try:
            lookup = getattr(importlib.import_module("helios.harness"), "get")
        except (ImportError, AttributeError) as exc:
            def lookup(name):
                raise ValueError("harness lookup unavailable")
        sessions.attach(cfg.hub, cfg.project.runs, args.bead, harness_lookup=lookup)
    except (OSError, TypeError, ValueError, KeyError) as exc:
        print(f"helios: {exc}", file=__import__("sys").stderr)
        return 2
    return 0
