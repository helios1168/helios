from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from helios.beads import Beads
from helios.config import load
from helios.gates import GateError, open_gates

NAME = "gate"
HELP = "list open gates"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true")


def run(args: argparse.Namespace) -> int:
    try:
        config = load(Path.cwd())
    except (ValueError, TypeError, RecursionError) as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
    try:
        gates = open_gates(Beads(config.hub))
    except GateError:
        print("helios: unexpected bd gate output", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(gates))
    else:
        for gate in gates:
            print(f"{gate['id']}: {', '.join(gate['blocks'])}")
    return 0
