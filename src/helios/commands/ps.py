from __future__ import annotations

import sys
from pathlib import Path

from helios import config, jsonio, sessions

NAME = "ps"
HELP = "List attempts."


def add_arguments(parser) -> None:
    parser.add_argument("--json", action="store_true", dest="as_json")


_LINE_BOUNDARY_ESCAPES = {
    "\x0b": "\\x0b",
    "\x0c": "\\x0c",
    "\x1c": "\\x1c",
    "\x1d": "\\x1d",
    "\x1e": "\\x1e",
    "\x85": "\\x85",
    "\u2028": "\\u2028",
    "\u2029": "\\u2029",
}


def _render_cell(value: object) -> str:
    """Render one text-output field so a row is always exactly one line (SPEC §9.2).

    Backslash and every character `str.splitlines` treats as a line boundary are escaped
    first, so a row can never be split or corrupted. What stdout's own encoding still cannot
    represent (a lone surrogate, or a non-ASCII character under a narrow encoding) is then
    replaced with its backslash escape.
    """
    text = "-" if value is None else str(value)
    text = text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")
    for char, escape in _LINE_BOUNDARY_ESCAPES.items():
        text = text.replace(char, escape)
    encoding = sys.stdout.encoding or "utf-8"
    return text.encode(encoding, "backslashreplace").decode(encoding)


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
        print(jsonio.dumps(rows, sort_keys=True))
        return 0
    columns = ("bead", "unit", "kind", "harness", "state", "attempt", "age", "worktree", "session")
    print("\t".join(columns))
    for row in rows:
        values = dict(row)
        if values["state"] == "launched" and not values["alive"]:
            values["state"] = "launched (dead)"
        print("\t".join(_render_cell(values[key]) for key in columns))
    return 0
