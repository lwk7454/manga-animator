"""SeriesStore protocol seam (vidgen pattern): pipeline depends on this, not
on the JSON implementation. Only the local implementation exists for v1."""

from __future__ import annotations

from pathlib import Path

from .series_store import SeriesStore

__all__ = ["SeriesStore"]


def default_data_dir() -> Path:
    """Package-root-relative default: <repo>/data (decision #016)."""
    from ..config import settings

    return settings().resolved_data_dir()
