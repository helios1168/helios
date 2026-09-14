"""The ``helios run`` command (SPEC §7)."""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Callable
from pathlib import Path

from helios import beads as beads_mod
from helios import config as config_mod
from helios import run as run_mod

NAME = "run"
HELP = "Run beads end to end through a harness (SPEC §7.1)."

_MEMORY_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,199}$")


def _valid_memory_key(key: str) -> bool:
    """SPEC §13's key rule; ``schema_version`` never exists as a memory (§7.1)."""
    return key != "schema_version" and _MEMORY_KEY_RE.fullmatch(key) is not None


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
    """Build the preflight memory lookup from the configured backend (§13).

    A key that fails the §13 key rule, or equals ``schema_version``, never
    exists on either backend, checked before any filesystem or ``bd`` call.
    """
    if cfg.memory.backend == "files":
        export = cfg.hub / cfg.memory.export_dir

        def has_file(key: str) -> bool:
            if not _valid_memory_key(key):
                return False
            try:
                names = os.listdir(export)
            except OSError:
                return False
            # Compare the exact file name from a directory listing, not
            # ``Path.is_file()``, so a case-insensitive filesystem never
            # matches a differently-cased key.
            return f"{key}.md" in names

        return has_file

    def has(key: str) -> bool:
        if not _valid_memory_key(key):
            return False
        try:
            return beads.recall(key) is not None
        except (RuntimeError, OSError, ValueError) as exc:
            raise run_mod.MemoryLookupError(
                f"helios: memory lookup failed for {key}: {exc}"
            ) from exc

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
