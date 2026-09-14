"""Run one claim and print one JSON line (SPEC §15.2 step 3).

Usage: python -m helios.claims.runner <module> <name>
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import traceback


def main(argv: list[str]) -> int:
    module_name, name = argv[1], argv[2]
    hub = os.getcwd()
    sys.path.insert(0, hub)
    sys.path.insert(0, os.path.join(hub, "src"))
    try:
        importlib.import_module(module_name)
        from helios.claims import claim_by_name

        func = claim_by_name(name).func
        if func is None:
            raise ValueError(f"claim {name!r} has no callable")
        result = func()
    except Exception:
        print(json.dumps({"error": traceback.format_exc()}))
        return 0
    from helios.envelope import Finding

    if result is True:
        print(json.dumps({"result": True}))
    elif result is False:
        print(json.dumps({"result": False}))
    elif isinstance(result, Finding):
        print(json.dumps({"finding": result.model_dump(mode="json")}))
    else:
        try:
            print(json.dumps({"result": result}))
        except TypeError:
            print(
                json.dumps(
                    {
                        "error": (
                            f"claim {name!r} returned non-JSON-serializable "
                            f"value of type {type(result).__name__}: {result!r}"
                        )
                    }
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
