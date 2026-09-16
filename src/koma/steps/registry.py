"""Registry services — candidates, sheets, bindings, approvals (PLAN §14).

Lifecycle (§14.1): revisions move proposed → approved | rejected. Approved
revisions are IMMUTABLE; an edit creates a new proposed revision. Production
colorization consumes only APPROVED sheet revisions bound to a panel with
exact (character, revision, variant, revision) identity (D05).

No circular approval (§14.2): an approved source/palette selection authorizes
a DRAFT sheet-generation task; the resulting sheet is itself a proposal until
the user approves that exact artifact. Rejected/unapproved revisions can
never enter production paths — enforced here, not by caller discipline.
"""

from __future__ import annotations

import json

from ..log import get_logger
from ..storage.domain_store import KomaStore
from ..storage.errors import BlockedPrerequisite, InvalidTransition, NotFound

log = get_logger("steps.registry")

SHEET_DRAFT = "sheet_draft"


def save_canonical_candidates(
    store: KomaStore,
    entity_id: str,
    candidates: list[dict],
    selected: dict,
) -> dict:
    """Persist canonical candidate pairing + the selected proposal (B26).

    Each candidate: {panel_id, artifact_relpath, score, reason}. The
    selected proposal must be one of the submitted candidates — a model
    returning a foreign ID is rejected (§14.1).
    """
    by_id = {c["panel_id"]: c for c in candidates}
    if selected.get("panel_id") not in by_id:
        raise ValueError(
            f"selected canonical panel {selected.get('panel_id')!r} "
            "is not in the submitted candidate set"
        )
    payload = {
        "candidates": sorted(candidates, key=lambda c: c["panel_id"]),  # deterministic tie-break
        "selected": selected,
    }
    return store.save_registry_proposal(
        entity_id=entity_id,
        kind="character",
        payload=payload,
        evidence_artifact_ids=[c["artifact_relpath"] for c in candidates],
    )


def approve_card(
    store: KomaStore, entity_id: str, revision: int
) -> dict:
    """Approve an exact registry revision (the card gate is the user's)."""
    return store.approve_registry_revision(entity_id, revision)


def reject_card(store: KomaStore, entity_id: str, revision: int) -> dict:
    return store.reject_registry_revision(entity_id, revision)


def request_draft_sheet(
    store: KomaStore, entity_id: str, character_revision: int, palette: dict
) -> dict:
    """An approved character revision authorizes a DRAFT sheet proposal (§14.2).

    The draft is a proposed VARIANT revision whose payload references the
    approved source; it cannot be consumed by production until its own
    artifact is approved.
    """
    row = store.registry_payload(entity_id, character_revision)
    if row["status"] != "approved":
        raise BlockedPrerequisite(
            f"character {entity_id}@{character_revision} is not approved; "
            "draft sheets require an approved source selection (no circular approval)"
        )
    variant_entity = f"{entity_id}:sheet:{row['revision']}"
    res = store.save_registry_proposal(
        entity_id=variant_entity,
        kind="variant",
        payload={"sheet_status": SHEET_DRAFT, "palette": palette,
                 "parent_character_revision": character_revision},
        parent_character_id=entity_id,
    )
    return res


def approve_sheet(
    store: KomaStore, entity_id: str, revision: int, sheet_artifact_id: str
) -> dict:
    """Approve the EXACT generated sheet artifact (§14.2: proposal → production).

    The sheet revision's payload is updated to record the approved artifact
    BEFORE approval flips — approval always refers to exact evidence.
    """
    row = store.registry_payload(entity_id, revision)
    if row["status"] != "proposed":
        raise InvalidTransition(f"sheet {entity_id}@{revision} not proposed")
    payload = json.loads(row["payload"])
    if payload.get("sheet_status") != SHEET_DRAFT:
        raise InvalidTransition("revision is not a sheet draft")
    payload["sheet_status"] = "approved"
    payload["sheet_artifact_id"] = sheet_artifact_id
    # persist the exact evidence, then flip status
    with store.db.transaction(immediate=True) as conn:
        conn.execute(
            "UPDATE registry_revisions SET payload = ?"
            " WHERE entity_id = ? AND revision = ? AND status = 'proposed'",
            (_dumps(payload), entity_id, revision),
        )
    return store.approve_registry_revision(entity_id, revision)


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def require_approved_character(
    store: KomaStore, entity_id: str, revision: int | None = None
) -> dict:
    """Production gate: the character revision must exist AND be approved."""
    row = store.registry_payload(entity_id, revision) if revision else store.latest_registry(
        entity_id
    )
    if row is None:
        raise NotFound(f"character {entity_id}")
    if row["status"] != "approved":
        raise BlockedPrerequisite(
            f"character {entity_id}@{row['revision']} is {row['status']}; "
            "production requires approved bindings (D05)"
        )
    return row


def require_approved_variant(
    store: KomaStore, entity_id: str, revision: int
) -> dict:
    row = store.registry_payload(entity_id, revision)
    if row["status"] != "approved":
        raise BlockedPrerequisite(
            f"variant {entity_id}@{revision} is {row['status']}; production "
            "consumes approved sheets only (D05)"
        )
    return row


def compile_colorize_reference(
    store: KomaStore, panel_id: str, character_id: str
) -> dict:
    """Resolve the reference inputs a colorize request may use for a panel.

    Returns the exact approved (character_revision, variant_id,
    variant_revision, sheet_artifact_id) or raises — unapproved references
    are a PREVIEW-only path (D05), never silently substituted.
    """
    binding = store.latest_panel_bindings(panel_id)
    if binding is None:
        raise BlockedPrerequisite(
            f"panel {panel_id} has no reference binding; production colorize "
            "requires approved bindings"
        )
    char_rev = binding.get("character_revision")
    if binding.get("character_id") != character_id or char_rev is None:
        raise BlockedPrerequisite(
            f"panel {panel_id} is not bound to {character_id}"
        )
    require_approved_character(store, character_id, char_rev)
    sheet = None
    if binding.get("variant_id"):
        vrow = require_approved_variant(store, binding["variant_id"],
                                        binding["variant_revision"])
        sheet = json.loads(vrow["payload"]).get("sheet_artifact_id")
        if not sheet:
            raise BlockedPrerequisite(
                f"variant {binding['variant_id']}@{binding['variant_revision']} "
                "has no approved sheet artifact"
            )
    return {
        "panel_id": panel_id,
        "character_id": character_id,
        "character_revision": char_rev,
        "variant_id": binding.get("variant_id"),
        "variant_revision": binding.get("variant_revision"),
        "sheet_artifact_id": sheet,
    }


def revised_sheet_invalidates_descendants(store: KomaStore, entity_id: str) -> list[str]:
    """§6.3: a new sheet revision invalidates only outputs consuming the old
    binding (their descendants), not unrelated characters or source/OCR.

    Returns work ids whose desired fingerprint no longer matches the new
    approved revision (they become replannable; nothing is deleted).
    """
    latest = store.latest_registry(entity_id)
    if latest is None or latest["status"] != "approved":
        return []
    stale: list[str] = []
    for w in store.find_work(stage="colorize"):
        spec = json.loads(w["desired_spec"])
        if spec.get("character_id") == entity_id and (
            spec.get("character_revision") != latest["revision"]
        ):
            store.set_work_state(w["id"], "superseded")
            stale.append(w["id"])
    return stale


__all__ = [
    "SHEET_DRAFT",
    "approve_card",
    "approve_sheet",
    "compile_colorize_reference",
    "reject_card",
    "request_draft_sheet",
    "require_approved_character",
    "require_approved_variant",
    "revised_sheet_invalidates_descendants",
    "save_canonical_candidates",
]
