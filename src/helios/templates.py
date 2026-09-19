"""Lookup for helios's shipped template files, packaged beside this module.

Same shape as ``stageset.RESEARCH_PATH``: the templates live under ``helios/templates/`` so they
ship as package data inside the wheel, instead of relative to a repository checkout that is not
there once helios is installed.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent / "templates"


def path(name: str) -> Path:
    """Return the path to ``helios/templates/<name>``, raising FileNotFoundError when absent."""
    candidate = TEMPLATES_DIR / name
    if not candidate.exists():
        raise FileNotFoundError(str(candidate))
    return candidate
