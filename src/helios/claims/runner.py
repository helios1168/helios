"""Run one claim and print one JSON protocol line (SPEC §15.2 step 3).

Usage: python -m helios.claims.runner <module> <name>

File descriptor 1 is redirected to file descriptor 2 while the claim module is
imported and the claim runs, so claim output never reaches the protocol
channel. The one protocol line goes to the saved original stdout.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import traceback


def run_claim(module_name: str, name: str) -> str:
    """Import the module, run the claim, return the protocol line."""
    from helios.claims import claim_by_name

    importlib.import_module(module_name)
    func = claim_by_name(name).func
    if func is None:
        raise ValueError(f"claim {name!r} has no callable")
    result = func()
    from helios.envelope import Finding

    if result is True:
        return json.dumps({"result": True})
    if result is False:
        return json.dumps({"result": False})
    if isinstance(result, Finding):
        return json.dumps({"finding": result.model_dump(mode="json")})
    return json.dumps({"other": type(result).__name__})


def main(argv: list[str]) -> int:
    module_name, name = argv[1], argv[2]
    hub = os.getcwd()
    sys.path.insert(0, hub)
    sys.path.insert(0, os.path.join(hub, "src"))
    sys.stdout.flush()
    sys.stderr.flush()
    saved = os.dup(1)
    os.dup2(2, 1)
    try:
        try:
            line = run_claim(module_name, name)
        except BaseException:
            line = json.dumps({"error": traceback.format_exc()})
    finally:
        try:
            sys.stdout.flush()
        except OSError:
            pass
        os.dup2(saved, 1)
        os.close(saved)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
