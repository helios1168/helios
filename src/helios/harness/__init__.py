"""Harness adapters: one module per agent CLI (SPEC §6). The interface is harness/base.py."""

from __future__ import annotations

from helios.harness.base import Harness


def get(name: str) -> Harness:
    """Return the adapter for ``name`` (SPEC §6.1)."""
    if name == "fake":
        from helios.harness.fake import FakeHarness

        return FakeHarness()
    try:
        import importlib

        mod = importlib.import_module(f"helios.harness.{name}")
    except ImportError as exc:
        raise ValueError(f"unknown harness {name!r}") from exc
    for attr in ("Harness", f"{name.capitalize()}Harness", "Adapter"):
        candidate = getattr(mod, attr, None)
        if candidate is not None:
            return candidate()
    harness = getattr(mod, "harness", None)
    if harness is not None:
        return harness() if callable(harness) else harness
    raise ValueError(f"harness module {name!r} has no adapter")
