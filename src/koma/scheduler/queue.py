"""Experimental file-backed job queue and pause flag.

Whole-file JSON updates do not provide transactional claims or cross-process
ownership. PLAN.md §§5-9 defines the required replacement. A missing remote
history entry must not be assumed safe to resubmit in the future recovery path."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..log import get_logger

log = get_logger("scheduler.queue")


def _state_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "state"


def queue_path(data_dir: Path) -> Path:
    return _state_dir(data_dir) / "queue.json"


def paused_path(data_dir: Path) -> Path:
    return _state_dir(data_dir) / "paused"


def load_queue(data_dir: Path) -> dict[str, Any]:
    p = queue_path(data_dir)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"jobs": []}


def save_queue(data_dir: Path, queue: dict[str, Any]) -> None:
    p = queue_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(queue, indent=2), encoding="utf-8")


def is_paused(data_dir: Path) -> bool:
    return paused_path(data_dir).exists()


def set_paused(data_dir: Path, paused: bool) -> None:
    p = paused_path(data_dir)
    if paused:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("drain-then-hold\n", encoding="utf-8")
    else:
        p.unlink(missing_ok=True)


def next_pending(queue: dict[str, Any]) -> dict[str, Any] | None:
    """First pending job (FIFO)."""
    for job in queue["jobs"]:
        if job.get("state") == "pending":
            return job
    return None


def running_job(queue: dict[str, Any]) -> dict[str, Any] | None:
    for job in queue["jobs"]:
        if job.get("state") == "running":
            return job
    return None
