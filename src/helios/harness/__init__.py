"""Harness adapters: one module per agent CLI (SPEC §6). The interface is harness/base.py."""

from __future__ import annotations

from typing import cast

from helios.harness.base import Harness

_REGISTERED = ("claude", "codex", "opencode", "agy", "fake")


def get(name: str) -> Harness:
    """Return the adapter for ``name`` (SPEC §6.1, §6.4)."""
    if name == "base":
        raise TypeError("helios.harness.base is the adapter interface, not a harness")
    if name == "fake":
        from helios.harness.fake import FakeHarness

        return FakeHarness()
    if name not in _REGISTERED:
        raise ValueError(f"unknown harness {name!r}")
    try:
        import importlib

        mod = importlib.import_module(f"helios.harness.{name}")
    except ImportError as exc:
        raise ValueError(f"unknown harness {name!r}") from exc
    for attr in ("Harness", f"{name.capitalize()}Harness", "Adapter"):
        candidate = getattr(mod, attr, None)
        if isinstance(candidate, type):
            return cast(Harness, candidate())
    harness_attr = getattr(mod, "harness", None)
    if isinstance(harness_attr, type):
        return cast(Harness, harness_attr())
    if isinstance(harness_attr, Harness):
        return harness_attr
    raise ValueError(f"unknown harness {name!r}")
