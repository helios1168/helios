"""Harness adapters: one module per agent CLI (SPEC §6). The interface is harness/base.py."""

from __future__ import annotations

from typing import cast

from helios.harness.base import Harness

# Explicit registry: name -> the adapter class defined in that module. Every
# adapter module also imports ``Harness`` itself (its base class), so
# searching the module's attributes for anything matching ``Harness`` would
# find that import instead of the concrete class; naming the class avoids it.
_ADAPTER_CLASS = {
    "claude": "ClaudeAdapter",
    "codex": "CodexAdapter",
    "opencode": "OpencodeAdapter",
    "agy": "AgyAdapter",
}


def get(name: str) -> Harness:
    """Return the adapter for ``name`` (SPEC §6.1, §6.4).

    Only the registered harness names resolve; any other name (including
    ``base``, the interface module) raises ``ValueError`` naming it.
    """
    if name == "fake":
        from helios.harness.fake import FakeHarness

        return FakeHarness()
    if name not in _ADAPTER_CLASS:
        raise ValueError(f"unknown harness {name!r}")
    import importlib

    mod = importlib.import_module(f"helios.harness.{name}")
    candidate = getattr(mod, _ADAPTER_CLASS[name])
    return cast(Harness, candidate())
