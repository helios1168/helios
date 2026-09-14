from __future__ import annotations

import importlib
import sys
from pathlib import Path

from helios import config, sessions

NAME = "attach"
HELP = "Attach to an attempt."


def add_arguments(parser) -> None:
    parser.add_argument("bead")


def run(args) -> int:
    if not sessions.BEAD_ID_RE.fullmatch(args.bead):
        print(f"helios: invalid bead id {args.bead}", file=sys.stderr)
        return 2
    try:
        cfg = config.load(Path.cwd())
        try:
            lookup = getattr(importlib.import_module("helios.harness"), "get")
        except (ImportError, AttributeError) as exc:
            def lookup(name):
                raise ValueError("harness lookup unavailable")
        sessions.attach(cfg.hub, cfg.project.runs, args.bead, harness_lookup=lookup)
    except (OSError, TypeError, ValueError, KeyError, RecursionError) as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
    return 0
