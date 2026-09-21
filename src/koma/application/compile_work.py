"""Shot-plan → compiled work orchestration (G1 + G3)."""

from __future__ import annotations

import json
from typing import Any

from ..adapters.reference_manifest import (
    build_reference_manifest,
    validate_manifest_against_capacity,
)
from ..application.composition import (
    DEFAULT_COMPOSITION_ROLE,
    ROLE_I2V_FIRST_FRAME,
    ROLE_R2V_REF,
    assert_exclusive_roles,
    composition_entry,
)
from ..i2v_compiler import compile_i2v_graph, require_first_frame_role
from ..r2v_compiler import compile_shot_graph, reference_upload_plan
from ..r2v_prompt import build_colored_r2v_prompt, build_i2v_choreography_prompt
from ..scenes import pick_primary_panel
from ..storage.domain_store import KomaStore
from ..storage.errors import BlockedPrerequisite, NotFound
from ..storage.shot_store import ShotStore
from ..util import canonical_hash


def bindings_for_shot(store: KomaStore, shot: dict[str, Any]) -> list[dict[str, Any]]:
    with store.db.transaction() as conn:
        rows = conn.execute(
            "SELECT b.*, a.kind FROM asset_occurrence_bindings b"
            " LEFT JOIN assets a ON a.id = b.asset_id"
            " WHERE b.series_id=? AND b.state='bound'",
            (shot["series_id"],),
        ).fetchall()
        latest = {}
        for row in rows:
            if row["shot_id"] != shot["shot_id"] and row["panel_id"] not in shot["source_panel_ids"]:
                continue
            key = (row["asset_id"], row["subject_kind"], row["panel_id"] or row["shot_id"])
            prev = latest.get(key)
            if prev is None or row["binding_revision"] > prev["binding_revision"]:
                latest[key] = dict(row)
        out = []
        for row in latest.values():
            rev = conn.execute(
                "SELECT artifact_ids FROM asset_revisions WHERE asset_id=? AND revision=?",
                (row["asset_id"], row["asset_revision"]),
            ).fetchone()
            approval = conn.execute(
                "SELECT state FROM asset_approvals WHERE asset_id=? AND revision=?",
                (row["asset_id"], row["asset_revision"]),
            ).fetchone()
            if rev is None or approval is None or approval["state"] != "approved":
                continue
            artifact_ids = json.loads(rev["artifact_ids"])
            if not artifact_ids:
                continue
            artifact = conn.execute(
                "SELECT id, sha256 FROM artifacts WHERE id=?", (artifact_ids[0],)
            ).fetchone()
            if artifact is None:
                continue
            out.append(
                {
                    "asset_id": row["asset_id"],
                    "asset_revision": row["asset_revision"],
                    "artifact_id": artifact["id"],
                    "sha256": artifact["sha256"],
                    "kind": row["kind"],
                }
            )
    return out


def panel_artifact(store: KomaStore, panel_id: str) -> dict[str, Any]:
    with store.db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM artifacts WHERE scope_key=? AND kind='panel' "
            "AND validation_status='validated' ORDER BY created_at DESC",
            (panel_id,),
        ).fetchone()
    if row is None:
        raise NotFound(f"validated panel artifact for {panel_id}")
    return dict(row)


def primary_panel_id(store: KomaStore, shot: dict[str, Any]) -> str:
    ids = list(shot["source_panel_ids"])
    if not ids:
        raise BlockedPrerequisite("shot has no source panels")
    geometry: dict[str, dict[str, int]] = {}
    with store.db.transaction() as conn:
        for pid in ids:
            row = conn.execute("SELECT bbox_w, bbox_h FROM panels WHERE id=?", (pid,)).fetchone()
            if row is not None:
                geometry[pid] = {"bbox_w": row["bbox_w"], "bbox_h": row["bbox_h"]}
    return pick_primary_panel(ids, geometry or None)
