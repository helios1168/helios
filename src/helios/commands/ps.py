from __future__ import annotations

import json
from pathlib import Path

from helios import config, sessions

NAME = "ps"
HELP = "List attempts."


def add_arguments(parser) -> None:
    parser.add_argument("--json", action="store_true", dest="as_json")


def run(args) -> int:
    cfg = config.load(Path.cwd())
    rows = sessions.rows(cfg.hub, cfg.project.runs)
    if args.as_json:
        print(json.dumps(rows, sort_keys=True))
        return 0
    columns = ("bead", "unit", "kind", "harness", "state", "attempt", "age", "worktree", "session")
    print("\t".join(columns))
    for row in rows:
        print("\t".join(str(row[key]) if row[key] is not None else "-" for key in columns))
    return 0
