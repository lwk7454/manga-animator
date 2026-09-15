"""Small shared utilities: canonical hashing, UUIDs, timestamps.

Canonical JSON (sort_keys, compact separators) is the only serialization used
for fingerprints/hashes — never Python's process-randomized ``hash()``
(PLAN.md §5.2). Hashing utilities are stdlib; no dependency added.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any


def utcnow_iso() -> str:
    """ISO-8601 UTC timestamp for storage columns."""
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    """UUID4 string; internal IDs for series/pages/work/attempts/artifacts."""
    return str(uuid.uuid4())


def canonical_json(obj: Any) -> str:
    """Deterministic compact JSON for hashing typed manifests."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(obj: Any) -> str:
    """SHA-256 hex of the canonical JSON form of ``obj``."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def file_sha256(path, chunk: int = 1 << 20) -> str:
    """SHA-256 of a file, streamed (hash large assets OUTSIDE db transactions)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


__all__ = ["canonical_hash", "canonical_json", "file_sha256", "new_id", "utcnow_iso"]
