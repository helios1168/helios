"""The ``helios run`` command (SPEC §7)."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from helios import beads as beads_mod
from helios import config as config_mod
from helios import run as run_mod

NAME = "run"
HELP = "Run beads end to end through a harness (SPEC §7.1)."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Parse bead ids and run options (SPEC §7)."""
    parser.add_argument("beads", nargs="+", help="Bead ids to run.")
    parser.add_argument("--harness", default=None, help="Override the harness.")
    parser.add_argument("--again", action="store_true", help="Reset the worktree.")
    parser.add_argument("--timeout", type=int, default=None, help="Per-turn timeout in seconds.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan; create nothing.")
    parser.add_argument(
        "--max-parallel", type=int, default=3, help="Beads to run at once."
    )


def memory_has_for(
    beads: beads_mod.Beads | beads_mod.FakeBeads, cfg: config_mod.Config
) -> Callable[[str], bool]:
    """Build the preflight memory lookup from the configured backend (§13)."""
    if cfg.memory.backend == "files":
        export = cfg.hub / cfg.memory.export_dir
        return lambda key: (export / f"{key}.md").is_file()

    def has(key: str) -> bool:
        try:
            return beads.recall(key) is not None
        except Exception:
            return False

    return has


def run(args: argparse.Namespace) -> int:
    """Load config, read beads, and run the pipeline (SPEC §7.1)."""
    hub = config_mod.find_hub(Path.cwd())
    cfg = config_mod.load(hub)
    beads = beads_mod.Beads(hub)
    return run_mod.run_many(
        list(args.beads),
        hub=hub,
        beads=beads,
        config=cfg,
        harness_override=args.harness,
        timeout_s=args.timeout,
        again=args.again,
        dry_run=args.dry_run,
        max_parallel=args.max_parallel,
        memory_has=memory_has_for(beads, cfg),
    )
