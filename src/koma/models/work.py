"""Typed work/attempt states and legal transitions (PLAN.md §9).

Work = a desired result identified by its input fingerprint. An attempt = one
execution of that result. A failed attempt never permanently deduplicates away
the logical work. One transition validator is used by all providers.
"""

from __future__ import annotations

import enum


class WorkState(enum.StrEnum):
    """Logical work lifecycle. Blocked/ready/active are the scheduling states."""

    BLOCKED = "blocked"
    READY = "ready"
    ACTIVE = "active"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class AttemptState(enum.StrEnum):
    """One execution of a work item.

    The first six are NONTERMINAL for the unique-active-attempt constraint.
    ``submission_uncertain`` holds the work: not eligible for normal retries
    until reconciliation resolves it (D06).
    """

    PREPARED = "prepared"
    SUBMITTING = "submitting"
    SUBMISSION_UNCERTAIN = "submission_uncertain"
    RUNNING = "running"
    DOWNLOADING = "downloading"
    VALIDATING = "validating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    STALE = "stale"


# Attempt states that count as "active" for the at-most-one rule. Terminal
# states (succeeded/failed/interrupted/stale) free the work for a new attempt.
NONTERMINAL_ATTEMPT_STATES: frozenset[AttemptState] = frozenset(
    {
        AttemptState.PREPARED,
        AttemptState.SUBMITTING,
        AttemptState.SUBMISSION_UNCERTAIN,
        AttemptState.RUNNING,
        AttemptState.DOWNLOADING,
        AttemptState.VALIDATING,
    }
)

TERMINAL_ATTEMPT_STATES: frozenset[AttemptState] = frozenset(
    {AttemptState.SUCCEEDED, AttemptState.FAILED, AttemptState.INTERRUPTED, AttemptState.STALE}
)

# Normal attempt path: prepared → submitting → running → downloading →
# validating → succeeded. Detours: uncertainty reconciliation, terminal outcomes.
ATTEMPT_TRANSITIONS: dict[AttemptState, frozenset[AttemptState]] = {
    AttemptState.PREPARED: frozenset(
        {AttemptState.SUBMITTING, AttemptState.FAILED, AttemptState.INTERRUPTED,
         AttemptState.STALE}
    ),
    AttemptState.SUBMITTING: frozenset(
        {AttemptState.SUBMISSION_UNCERTAIN, AttemptState.RUNNING, AttemptState.DOWNLOADING,
         AttemptState.FAILED, AttemptState.INTERRUPTED}
    ),
    # Reconciliation may resolve uncertainty to running, downloading (already
    # finished remotely), or a proven terminal outcome — never straight to
    # prepared (a permitted retry allocates a NEW attempt number instead).
    AttemptState.SUBMISSION_UNCERTAIN: frozenset(
        {AttemptState.RUNNING, AttemptState.DOWNLOADING, AttemptState.FAILED,
         AttemptState.INTERRUPTED, AttemptState.STALE}
    ),
    AttemptState.RUNNING: frozenset(
        {AttemptState.DOWNLOADING, AttemptState.FAILED, AttemptState.INTERRUPTED,
         AttemptState.STALE}
    ),
    AttemptState.DOWNLOADING: frozenset(
        {AttemptState.VALIDATING, AttemptState.FAILED, AttemptState.INTERRUPTED}
    ),
    AttemptState.VALIDATING: frozenset(
        {AttemptState.SUCCEEDED, AttemptState.FAILED, AttemptState.STALE}
    ),
    # Terminal states never reset to prepared.
    AttemptState.SUCCEEDED: frozenset(),
    AttemptState.FAILED: frozenset(),
    AttemptState.INTERRUPTED: frozenset(),
    AttemptState.STALE: frozenset(),
}

# Work transitions. blocked → ready only when prerequisites hold; ready →
# active on atomic claim; active → succeeded only on promotion.
WORK_TRANSITIONS: dict[WorkState, frozenset[WorkState]] = {
    WorkState.BLOCKED: frozenset({WorkState.READY, WorkState.CANCELLED, WorkState.SUPERSEDED}),
    WorkState.READY: frozenset(
        {WorkState.ACTIVE, WorkState.BLOCKED, WorkState.CANCELLED, WorkState.SUPERSEDED}
    ),
    WorkState.ACTIVE: frozenset(
        {WorkState.SUCCEEDED, WorkState.FAILED, WorkState.BLOCKED, WorkState.CANCELLED,
         WorkState.SUPERSEDED}
    ),
    WorkState.FAILED: frozenset(
        {WorkState.READY, WorkState.BLOCKED, WorkState.CANCELLED, WorkState.SUPERSEDED}
    ),
    WorkState.SUCCEEDED: frozenset({WorkState.SUPERSEDED}),
    WorkState.CANCELLED: frozenset(),
    WorkState.SUPERSEDED: frozenset(),
}


def can_attempt_transition(current: AttemptState, target: AttemptState) -> bool:
    if current == target:
        return True  # idempotent writes
    return target in ATTEMPT_TRANSITIONS[current]


def can_work_transition(current: WorkState, target: WorkState) -> bool:
    if current == target:
        return True  # idempotent writes
    return target in WORK_TRANSITIONS[current]


def is_nonterminal_attempt(state: AttemptState) -> bool:
    return state in NONTERMINAL_ATTEMPT_STATES
