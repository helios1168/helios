"""helios command line (SPEC §2.3).

Subcommands are discovered from the modules in helios.commands, so adding a command never edits
this file. A command module defines:

    NAME: str                      # "run", or a nested path such as "unit new"
    HELP: str                      # one line
    def add_arguments(parser: argparse.ArgumentParser) -> None
    def run(args: argparse.Namespace) -> int

A nested NAME creates the intermediate group parsers on demand. A name may not be both a
command and a group ("unit" and "unit new" together is an error).
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil
import sys
from collections.abc import Iterable, Sequence
from types import ModuleType

from helios import __version__, commands, config


def discover() -> list[ModuleType]:
    """Import every public module in helios.commands, sorted by module name."""
    found = sorted(pkgutil.iter_modules(commands.__path__), key=lambda m: m.name)
    return [
        importlib.import_module(f"{commands.__name__}.{m.name}")
        for m in found
        if not m.name.startswith("_")
    ]


def build_parser(modules: Iterable[ModuleType] | None = None) -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="helios", description="Research workflow kit.")
    root.add_argument("--version", action="store_true", help="Print version and exit.")
    parsers: dict[tuple[str, ...], argparse.ArgumentParser] = {(): root}
    groups: dict[tuple[str, ...], argparse._SubParsersAction] = {}
    commands_seen: set[tuple[str, ...]] = set()

    def subparsers(path: tuple[str, ...]) -> argparse._SubParsersAction:
        if path in commands_seen:
            raise ValueError(f"'{' '.join(path)}' is a command and cannot also be a group")
        if path not in groups:
            dest = "command" if not path else "_group_" + "_".join(path)
            groups[path] = parsers[path].add_subparsers(dest=dest, metavar="<command>")
        return groups[path]

    for mod in discover() if modules is None else modules:
        path = tuple(mod.NAME.split())
        if not path:
            raise ValueError(f"{mod.__name__}: empty NAME")
        for i in range(1, len(path)):
            prefix = path[:i]
            if prefix not in parsers:
                parsers[prefix] = subparsers(prefix[:-1]).add_parser(
                    prefix[-1], help=f"{' '.join(prefix)} commands"
                )
        if path in parsers or path in groups:
            raise ValueError(f"duplicate or conflicting command '{mod.NAME}'")
        parser = subparsers(path[:-1]).add_parser(path[-1], help=mod.HELP)
        mod.add_arguments(parser)
        parser.set_defaults(_run=mod.run)
        parsers[path] = parser
        commands_seen.add(path)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "version", False):
        print(f"helios {__version__}")
        return 0
    run = getattr(args, "_run", None)
    if run is None:
        parser.print_help()
        return 2
    try:
        return int(run(args) or 0)
    except config.ConfigError as exc:
        # One handler for every command, since a configuration refusal reads the same whichever
        # command asked for the file (SPEC §2.3). Commands with their own handler never get here.
        print(f"helios: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
