"""Production route gate shared by the worker (G2)."""

from __future__ import annotations

from ..storage.errors import BlockedPrerequisite
from ..storage.shot_store import ShotStore

ALLOWED_COMPOSITION_ROLES = frozenset(
    {"composition_r2v_ref", "composition_i2v_first_frame"}
)


def check_production_route(db, shot_revision_id: str, ratio: str, adapter_role: str) -> dict:
    if not shot_revision_id:
        raise BlockedPrerequisite("production work requires shot_revision_id")
    ready = ShotStore(db).require_production_ready(shot_revision_id, ratio)
    if ready["route"] == "direct_r2v":
        return ready
    if ready["route"] != "composition_assisted":
        raise BlockedPrerequisite(f"unsupported production route {ready['route']}")
    if adapter_role not in ALLOWED_COMPOSITION_ROLES:
        raise BlockedPrerequisite(
            "composition-assisted work requires an exclusive adapter role"
        )
    return ready


__all__ = ["ALLOWED_COMPOSITION_ROLES", "check_production_route"]
