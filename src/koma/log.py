"""Logging — namespaced stdlib loggers (koma keeps it simple)."""

from __future__ import annotations

import logging


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"koma.{name}")
