"""Shot-plan to compiled work orchestration (G1 + G3)."""

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
            if (row["shot_id"] != shot["shot_id"]
                    and row["panel_id"] not in shot["source_panel_ids"]):
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


def compile_colored_work(
    store: KomaStore,
    shot_revision_id: str,
    ratio: str,
    *,
    caption: str = "",
    seed: int = 42,
    frames: int = 124,
    variant: str = "turbo4",
    adapter_role: str | None = None,
) -> dict[str, Any]:
    shots = ShotStore(store.db)
    shot = shots.get_shot_revision(shot_revision_id)
    ready = shots.require_production_ready(shot_revision_id, ratio)
    spec = shots.get_ratio_spec(shot_revision_id, ratio)
    panel = panel_artifact(store, primary_panel_id(store, shot))
    bindings = bindings_for_shot(store, shot)
    manifest = build_reference_manifest(
        panel_artifact_id=panel["id"],
        panel_sha256=panel["sha256"],
        character_bindings=[],
        object_bindings=[b for b in bindings if b.get("kind") == "object"],
        background_bindings=[b for b in bindings if b.get("kind") == "background"],
        appearance_bindings=[b for b in bindings if b.get("kind") == "character"],
    )
    route = shot["route"]
    role = adapter_role or ""
    raw_profile = spec.get("generation_profile") or ""
    if isinstance(raw_profile, str) and raw_profile.startswith("{"):
        try:
            role = role or json.loads(raw_profile).get("adapter_role", "")
        except json.JSONDecodeError:
            role = role
    composition = None
    if route == "composition_assisted":
        role = role or DEFAULT_COMPOSITION_ROLE
        composition = _composition_from_spec(spec, role)
        manifest = _attach_composition(manifest, composition)
        assert_exclusive_roles(manifest)
    validate_manifest_against_capacity(manifest)
    panel_path = store.db.data_dir / panel["relative_path"]
    upload_plan = reference_upload_plan(manifest, panel_path)
    upload_names = {u["artifact_id"]: u["upload_name"] for u in upload_plan}
    if route == "composition_assisted" and role == ROLE_I2V_FIRST_FRAME:
        first = require_first_frame_role(manifest)
        prompt = build_i2v_choreography_prompt(caption)
        graph = compile_i2v_graph(
            first_frame_upload=upload_names[first["artifact_id"]],
            prompt=prompt,
            width=spec["target_width"],
            height=spec["target_height"],
            frames=frames,
            seed=seed,
        )
    else:
        prompt = build_colored_r2v_prompt(manifest, caption=caption)
        graph = compile_shot_graph(
            variant=variant,
            panel_path=panel_path,
            reference_manifest=manifest,
            prompt=prompt,
            width=spec["target_width"],
            height=spec["target_height"],
            frames=frames,
            seed=seed,
            upload_names=upload_names,
        )
    desired = {
        "shot_revision_id": shot_revision_id,
        "compiled_graph": graph,
        "upload_plan": upload_plan,
        "reference_manifest": manifest,
        "seed": seed,
        "frames": frames,
        "caption": caption,
        "route": route,
        "adapter_role": role,
        "prompt": prompt,
    }
    fingerprint = canonical_hash(
        {
            "shot": shot_revision_id,
            "ratio": ratio,
            "manifest": manifest,
            "seed": seed,
            "frames": frames,
            "route": route,
            "adapter_role": role,
            "graph_variant": variant if role != ROLE_I2V_FIRST_FRAME else "i2v_turbo_v2",
        }
    )
    planned = store.plan_work(
        [
            {
                "series_id": shot["series_id"],
                "chapter_id": shot["chapter_id"],
                "scope_key": f"{shot['shot_id']}/{ratio}",
                "stage": "motion",
                "ratio": ratio,
                "mode": "production",
                "input_fingerprint": fingerprint,
                "desired_spec": desired,
                "priority": 100,
            }
        ]
    )[0]
    persistable = [m for m in manifest if m["role"] not in {ROLE_I2V_FIRST_FRAME, ROLE_R2V_REF}]
    profile = spec.get("generation_profile") or variant
    if composition is not None:
        profile = json.dumps(
            {
                "adapter_role": role,
                "artifact_id": composition["artifact_id"],
                "sha256": composition["sha256"],
            }
        )
    with store.db.transaction() as conn:
        conn.execute(
            "UPDATE shot_ratio_specs SET reference_manifest=?, generation_profile=?"
            " WHERE shot_revision_id=? AND ratio=?",
            (json.dumps(persistable), profile, shot_revision_id, ratio),
        )
    return {
        "work_id": planned["work_id"],
        "created": planned["created"],
        "state": planned["state"],
        "input_fingerprint": fingerprint,
        "manifest": manifest,
        "route": route,
        "adapter_role": role,
        "ready": ready,
    }


def plan_chapter_work(
    store: KomaStore, chapter_id: str, ratios: list[str], *, caption: str = ""
) -> list[dict[str, Any]]:
    with store.db.transaction() as conn:
        rows = conn.execute(
            "SELECT id FROM shot_plan_revisions WHERE chapter_id=? ORDER BY created_at, id",
            (chapter_id,),
        ).fetchall()
    return [
        compile_colored_work(store, row["id"], ratio, caption=caption)
        for row in rows
        for ratio in ratios
    ]


def _composition_from_spec(spec: dict[str, Any], role: str) -> dict[str, Any]:
    for entry in spec.get("reference_manifest") or []:
        if entry.get("role") == role:
            return entry
    raw = spec.get("generation_profile") or ""
    if isinstance(raw, str) and raw.startswith("{ "[:1]):
        payload = json.loads(raw)
        if payload.get("adapter_role") == role and payload.get("artifact_id"):
            return composition_entry(
                artifact_id=payload["artifact_id"],
                sha256=payload["sha256"],
                role=role,
                ratio=spec["ratio"],
            )
    raise BlockedPrerequisite(
        f"composition-assisted {role} artifact is missing from the ratio spec"
    )


def _attach_composition(
    manifest: list[dict[str, Any]], composition: dict[str, Any]
) -> list[dict[str, Any]]:
    if composition["role"] == ROLE_I2V_FIRST_FRAME:
        return [*manifest, {**composition, "slot_key": "first_frame", "prompt_tag": ""}]
    extra = {**composition, "slot_key": "ref_image_1", "prompt_tag": "<Picture 2>"}
    rest = []
    for entry in manifest[1:]:
        idx = len(rest) + 2
        rest.append({**entry, "slot_key": f"ref_image_{idx}", "prompt_tag": f"<Picture {idx + 1}>"})
    return [manifest[0], extra, *rest]


__all__ = [
    "bindings_for_shot",
    "compile_colored_work",
    "panel_artifact",
    "plan_chapter_work",
    "primary_panel_id",
]
