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
        print("helios: invalid learned options", file=__import__("sys").stderr)
        return 2
    try:
        beads = Beads(load(Path.cwd()).hub)
    except (ValueError, TypeError, RecursionError) as exc:
        print(f"helios: {exc}", file=__import__("sys").stderr)
        return 2
    if args.mark:
        try:
            return queue.mark(beads, args.mark, args.decision)
        except ValueError as exc:
            print(f"helios: {exc}", file=__import__("sys").stderr)
            return 2
    lines = queue.list_lines(beads, args.unit)
    if args.json:
        print(json.dumps([line.as_json() for line in lines]))
    else:
        current_unit: str | None = None
        current_bead: str | None = None
        current_attempt: int | None = None
        for line in lines:
            if line.unit != current_unit:
                print(f"unit: {line.unit}")
                current_unit, current_bead, current_attempt = line.unit, None, None
            if line.bead != current_bead:
                print(f"  bead: {line.bead}")
                current_bead, current_attempt = line.bead, None
            if line.attempt != current_attempt:
                print(f"    attempt: {line.attempt}")
                current_attempt = line.attempt
            print(f"      {line.kind}#{line.k}: {line.text}")
    return 0
