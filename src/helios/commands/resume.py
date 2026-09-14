"""The ``helios resume`` command (SPEC §9.2)."""

from __future__ import annotations

import sys
from pathlib import Path

from helios import beads as beads_mod
from helios import config as config_mod
from helios import resume as resume_mod
from helios import sessions

NAME = "resume"
HELP = "Resume a session with delivered messages or text (SPEC §9.2)."


def add_arguments(parser) -> None:
    parser.add_argument("bead")
    parser.add_argument("text", nargs="?", default=None)


def run(args) -> int:
    if not sessions.BEAD_ID_RE.fullmatch(args.bead):
        print(f"helios: invalid bead id {args.bead}", file=sys.stderr)
        return 2
    try:
        cfg = config_mod.load(Path.cwd())
        beads = beads_mod.Beads(cfg.hub)
        return resume_mod.resume(args.bead, args.text, hub=cfg.hub, beads=beads, config=cfg)
    except resume_mod.ResumeRefusal as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
    except (OSError, TypeError, ValueError, RecursionError) as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
