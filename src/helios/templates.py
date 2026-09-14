"""Lookup for files under the repo's ``templates/`` directory."""

from __future__ import annotations

from pathlib import Path


def path(name: str) -> Path:
    """Return the path to ``templates/<name>``, raising FileNotFoundError when absent."""
    candidate = Path(__file__).resolve().parents[2] / "templates" / name
    if not candidate.exists():
        raise FileNotFoundError(str(candidate))
    return candidate
