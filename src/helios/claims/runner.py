"""Run one claim and print one JSON protocol line (SPEC §15.2 step 3).

Usage: python -m helios.claims.runner <module> <name>

The runner imports <module> fresh (forgetting it, its submodules and their
claim registrations first, so collection here sees exactly one import pass,
same as the check/attack process), then looks up the single collected record
named <name> under <module> or its submodules. Zero or more than one match is
an error naming the claim (SPEC §15.2, decided): a claim registered by a
foreign module must never shadow the one the parent validated.

File descriptor 1 is redirected to file descriptor 2 while the claim module is
imported and the claim runs, so claim output never reaches the protocol
channel. The one protocol line is written with os.write to the saved original
stdout, then the process leaves with os._exit, so buffered forgeries and
atexit hooks never run. C stdio is flushed first so C output lands on stderr.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import traceback


def run_claim(module_name: str, name: str) -> str:
    """Import the module, run the named claim, return the protocol line."""
    from helios.claims import claim_for, reset_claims_module

    reset_claims_module(module_name)
    importlib.import_module(module_name)
    func = claim_for(module_name, name).func
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
    """Flush libc stdio so C output lands where fd 1 points (stderr here).

    Best effort: ctypes is imported here, not at module level, so an
    interpreter where ctypes cannot import still runs the claim (SPEC §15.2,
    decided).
    """
    try:
        import ctypes

        ctypes.CDLL(None).fflush(None)
    except Exception:
        pass


def main(argv: list[str]) -> None:
    module_name = argv[1]
    name = argv[2]
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
