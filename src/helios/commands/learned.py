from __future__ import annotations

import argparse
import json
from pathlib import Path

from helios.beads import Beads
from helios.config import load
from helios import learned as queue

NAME = "learned"
HELP = "list learned queue entries"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--unit")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--mark")
    parser.add_argument("decision", nargs="?")


def run(args: argparse.Namespace) -> int:
    if args.mark and (args.unit or args.json) or bool(args.mark) != bool(args.decision):
        return 2
    beads = Beads(load(Path.cwd()).hub)
    if args.mark:
        try:
            return queue.mark(beads, args.mark, args.decision)
        except ValueError:
            return 2
    lines = queue.list_lines(beads, args.unit)
    if args.json:
        print(json.dumps([line.as_json() for line in lines]))
    else:
        for line in lines:
            print(f"{line.unit}\t{line.bead}\t{line.attempt}\t{line.kind}#{line.k}\t{line.text}")
    return 0
