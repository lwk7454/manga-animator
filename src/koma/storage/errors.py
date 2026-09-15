"""Typed store outcomes (PLAN.md §5.4): every mutable store method must
distinguish not-found, revision-conflict, invalid-transition and
blocked-prerequisite outcomes instead of raising bare exceptions everywhere.

Domain code raises these; the CLI maps them to exit codes (§10).
"""

from __future__ import annotations


class StoreError(RuntimeError):
    """Base class for typed storage outcomes."""


class NotFound(StoreError):
    """The referenced entity does not exist (or is tombstoned)."""


class RevisionConflict(StoreError):
    """CAS failure: expected revision does not match the current revision."""


class InvalidTransition(StoreError):
    """The requested state move is not legal (lifecycle violation)."""


class BlockedPrerequisite(StoreError):
    """A required prerequisite (approval, artifact, service) is missing/held."""


class DuplicateOperation(StoreError):
    """A conflicting write already exists for this identity/operation key."""


__all__ = [
    "BlockedPrerequisite",
    "DuplicateOperation",
    "InvalidTransition",
    "NotFound",
    "RevisionConflict",
    "StoreError",
]
