"""Run one claim and print one JSON protocol line (SPEC §15.2 step 3).

Usage: python -m helios.claims.runner <module> <name>

File descriptor 1 is redirected to file descriptor 2 while the claim module is
imported and the claim runs, so claim output never reaches the protocol
channel. The one protocol line is written with os.write to the saved original
stdout, then the process leaves with os._exit, so buffered forgeries and
atexit hooks never run. C stdio is flushed first so C output lands on stderr.
"""

from __future__ import annotations

import ctypes
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


def flush_c_stdio() -> None:
    """Flush libc stdio so C output lands where fd 1 points (stderr here)."""
    try:
        ctypes.CDLL(None).fflush(None)
    except Exception:
        pass


def main(argv: list[str]) -> None:
    module_name, name = argv[1], argv[2]
    hub = os.getcwd()
    sys.path.insert(0, hub)
    sys.path.insert(0, os.path.join(hub, "src"))
    sys.stdout.flush()
    sys.stderr.flush()
    saved = os.dup(1)
    os.dup2(2, 1)
    try:
        line = run_claim(module_name, name)
    except BaseException:
        line = json.dumps({"error": traceback.format_exc()})
    flush_c_stdio()
    os.write(saved, (line + "\n").encode("utf-8"))
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv)
