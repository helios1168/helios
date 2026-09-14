from __future__ import annotations

import json
import sys
from pathlib import Path

from helios import config, sessions

NAME = "ps"
HELP = "List attempts."


def add_arguments(parser) -> None:
    parser.add_argument("--json", action="store_true", dest="as_json")


def _render_cell(value: object) -> str:
    """Render one text-output field so a row is always exactly one line (SPEC §9.2).

    Backslash and the control characters that would otherwise split or corrupt a row are
    escaped first, then anything stdout still cannot encode (a lone surrogate) is replaced
    with its backslash escape.
    """
    text = "-" if value is None else str(value)
    text = text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def run(args) -> int:
    try:
        cfg = config.load(Path.cwd())
    except (OSError, TypeError, ValueError, RecursionError) as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 2
    try:
        rows = sessions.rows(cfg.hub, cfg.project.runs)
    except OSError as exc:
        print(f"helios: {exc}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(rows, sort_keys=True, allow_nan=False))
        return 0
    columns = ("bead", "unit", "kind", "harness", "state", "attempt", "age", "worktree", "session")
    print("\t".join(columns))
    for row in rows:
        values = dict(row)
        if values["state"] == "launched" and not values["alive"]:
            values["state"] = "launched (dead)"
        print("\t".join(_render_cell(values[key]) for key in columns))
    return 0
