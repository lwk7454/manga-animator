"""Run-level pause/drain/resume control flow (PLAN.md §8).

Separates REQUESTED state (user intent) from OBSERVED state (what actually
happened). A run may be ``pausing`` while an attempt is still active; the
run loop drains (finishes/checkpoints the current bounded operation), then
releases managed services and verifies release — only then ``paused`` with
an observed ``resources_state``.

Control requests flow through the store (owner-targeted, idempotent); this
module implements the run-side handling of those requests for the unified
run loop (P13). Resource release is measured through gpu.resources; when no
backend is implemented for a mode the state stays ``unknown``/pending,
never "success" (§7.2 P02 contract carried over).
"""

from __future__ import annotations

from typing import Any

from ..gpu.resources import ReleaseState, ResourceManager
from ..log import get_logger
from ..storage.domain_store import KomaStore
from ..storage.errors import RevisionConflict

log = get_logger("application.control")

# Run status values are constrained by the schema CHECK; transitions here
# follow §8.1: created → running → pausing → paused / complete / failed ...
RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"running", "cancelled"}),
    "running": frozenset({"pausing", "waiting_review", "waiting_service", "held",
                          "interrupted", "failed", "complete", "cancelled"}),
    "pausing": frozenset({"paused", "running", "cancelled", "failed"}),
    "paused": frozenset({"running", "cancelled"}),
    "waiting_review": frozenset({"running", "cancelled"}),
    "waiting_service": frozenset({"running", "paused", "cancelled"}),
    "held": frozenset({"running", "paused", "cancelled"}),
    "interrupted": frozenset({"running", "cancelled"}),
    "failed": frozenset(),
    "complete": frozenset(),
    "cancelled": frozenset(),
}


def can_run_transition(current: str, target: str) -> bool:
    if current == target:
        return True
    return target in RUN_TRANSITIONS.get(current, frozenset())


class RunController:
    """Pause/resume/stop for the owning process, over the store + resources."""

    def __init__(self, store: KomaStore, resources: ResourceManager):
        self.store = store
        self.resources = resources

    def set_run_status(self, run_id: str, target: str, **extra: Any) -> dict:
        run = self.store.get_run(run_id)
        if not can_run_transition(run["status"], target):
            raise RevisionConflict(
                f"illegal run transition {run['status']} → {target}"
            )
        fields: dict[str, Any] = {"status": target}
        for key in ("resources_state", "resources_observed_at", "last_summary",
                    "requested_control"):
            if key in extra:
                fields[key] = extra[key]
        return self.store.update_run(run_id, **fields)

    def request_pause(self, run_id: str, *, immediate: bool = False) -> dict:
        """§8.2/§8.3: acknowledge intent; drain/verify is the caller's loop.

        ``immediate`` marks the interrupted-restart warning; the run loop
        still owns the actual interruption mechanics.
        """
        self.resources.request_pause()
        run = self.store.get_run(run_id)
        return self.store.update_run(
            run_id,
            status="pausing",
            requested_control="pause_now" if immediate else "pause",
        ) if run["status"] in ("created", "running", "pausing") else run

    def acknowledge_pause(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        if run["status"] == "pausing":
            self.store.update_run(run_id, requested_control="pause")

    def complete_pause(self, run_id: str, release_obs: str) -> dict:
        """Mark paused with the OBSERVED release state (never a bare flag)."""
        if release_obs in ("active", "draining", "releasing"):
            raise RevisionConflict(f"cannot complete pause while resources {release_obs}")
        return self.store.update_run(
            run_id,
            status="paused",
            resources_state=release_obs,
            resources_observed_at=utcnow(),
        )

    def resume_run(self, run_id: str) -> dict:
        run = self.store.get_run(run_id)
        if run["status"] not in ("paused", "waiting_review", "held", "interrupted",
                                 "waiting_service"):
            raise RevisionConflict(f"run {run_id} not resumable from {run['status']}")
        self.resources.resume()
        return self.store.update_run(
            run_id, status="running", requested_control=None,
            resources_state=ReleaseState.ACTIVE.value, resources_observed_at=utcnow(),
        )

    def drain_and_release(self, run_id: str, service_id: str) -> str:
        """Default pause tail: no new claims; verify release; complete pause.

        Returns the observed release state string written to the run. Never
        prints "GPU released" from intent alone (§8.1).
        """
        self.resources.request_pause()
        self.store.update_run(run_id, resources_state=ReleaseState.DRAINING.value,
                              resources_observed_at=utcnow())
        obs = self.resources.release(service_id)
        state = obs.release_state.value
        self.complete_pause(run_id, state)
        return state


def utcnow() -> str:
    from ..util import utcnow_iso

    return utcnow_iso()


__all__ = ["RUN_TRANSITIONS", "RunController", "can_run_transition"]
