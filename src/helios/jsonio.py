"""Strict JSON load and dump helpers shared by every harness output and helios
artifact (decision, hel-cwf; SPEC text lands in a later batch, see SPEC §6.2,
§6.4 and §13 for the sites that use this module).

A JSON number that overflows to an infinite float (``1e999``, ``-1e999``) is
not JSON, exactly like ``NaN``, ``Infinity`` and ``-Infinity``: all five are
refused by ``loads``. ``dumps`` always writes with ``allow_nan=False``, so a
non-finite float can never be serialized either.
"""

from __future__ import annotations

import json
import math
from typing import Any


def _reject_constant(name: str) -> Any:
    """``parse_constant``: NaN, Infinity and -Infinity are not JSON."""
    raise ValueError(f"invalid constant {name!r}")


def _strict_float(text: str) -> float:
    """``parse_float``: refuse a numeral that overflows to a non-finite float."""
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"number {text!r} overflows to a non-finite float")
    return value


def loads(data: str | bytes, **kwargs: Any) -> Any:
    """Parse JSON, refusing NaN, Infinity, -Infinity and any overflowing number.

    ``parse_constant`` and ``parse_float`` are always the strict ones above;
    any value passed for them is ignored.
    """
    kwargs["parse_constant"] = _reject_constant
    kwargs["parse_float"] = _strict_float
    return json.loads(data, **kwargs)


def dumps(obj: Any, **kwargs: Any) -> str:
    """Serialize JSON with ``allow_nan=False`` always in force.

    A caller asking for ``allow_nan=True`` gets a ``ValueError`` instead:
    this module never writes a non-finite float.
    """
    if kwargs.get("allow_nan"):
        raise ValueError("jsonio.dumps: allow_nan=True is refused")
    kwargs["allow_nan"] = False
    return json.dumps(obj, **kwargs)
