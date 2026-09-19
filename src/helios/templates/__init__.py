"""Lookup for helios's shipped template files, which sit in this package directory.

Same reason as ``stageset.RESEARCH_PATH``: the templates ship as package data inside the wheel
instead of being read relative to a repository checkout, which is not there once helios is
installed. This is a package rather than a module beside a ``templates/`` data directory so that
the two cannot shadow each other.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent


def path(name: str) -> Path:
    """Return the path to ``helios/templates/<name>``, raising FileNotFoundError when absent."""
    candidate = TEMPLATES_DIR / name
    if not candidate.exists():
        raise FileNotFoundError(str(candidate))
    return candidate
