"""Public seam for the current JSON SeriesStore.

The SQLite replacement and transactional API contract are planned in PLAN.md §5."""

from __future__ import annotations

from pathlib import Path

from .series_store import SeriesStore

__all__ = ["SeriesStore"]


def default_data_dir() -> Path:
    """Package-root-relative default: <repo>/data."""
    from ..config import settings

    return settings().resolved_data_dir()
