"""R3 — deterministic labelled reference manifest builder (PLAN §E).

Maps input slot → artifact/asset revision → semantic role. Enforces the
installed node's reference capacity (verified live: max 9 ref images via
COMFY_AUTOGROW_V3 template on MiniMaxH3ReferenceToVideo). Never silently
drops overflow — the caller must split the shot or hold for review.
"""

from __future__ import annotations

import re
from typing import Any

MAX_REF_IMAGES = 9  # installed schema: ref_images max 9
MAX_REF_VIDEOS = 3
MAX_REF_AUDIOS = 3

STORY_ROLE = "story_composition"
APPEARANCE_ROLE = "appearance_evidence"
I2V_FIRST_FRAME_ROLE = "composition_i2v_first_frame"
R2V_COMPOSITION_ROLE = "composition_r2v_ref"
R2V_ROLES = frozenset(
    {
        STORY_ROLE,
        APPEARANCE_ROLE,
        R2V_COMPOSITION_ROLE,
        "character_identity",
        "object_identity",
        "background_identity",
    }
)


class ReferenceManifestError(ValueError):
    pass


def build_reference_manifest(
    *,
    panel_artifact_id: str,
    panel_sha256: str,
    character_bindings: list[dict[str, Any]],
    object_bindings: list[dict[str, Any]] | None = None,
    background_bindings: list[dict[str, Any]] | None = None,
    appearance_bindings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Slot order is deterministic: Picture 1 = panel, then appearance
    evidence, then leftover identity bindings, then objects, then backgrounds.

    Each entry: {slot_key, prompt_tag, role, entity, artifact_id, sha256}.
    prompt_tag is ONE-BASED; Picture 1 is the first panel reference.
    """
    manifest: list[dict[str, Any]] = [
        {
            "slot_key": "ref_image_0",
            "prompt_tag": "<Picture 1>",
            "role": STORY_ROLE,
            "entity": None,
            "artifact_id": panel_artifact_id,
            "sha256": panel_sha256,
        }
    ]
    ordered: list[tuple[str, str, dict[str, Any]]] = []
    for role, bindings in (
        (APPEARANCE_ROLE, appearance_bindings or []),
        ("character_identity", character_bindings),
        ("object_identity", object_bindings or []),
        ("background_identity", background_bindings or []),
    ):
        for b in sorted(bindings, key=lambda x: (x["asset_id"], x["asset_revision"])):
            ordered.append((role, f"{b['asset_id']}", b))
    for role, entity, b in ordered:
        if _r2v_count(manifest) >= MAX_REF_IMAGES:
            raise ReferenceManifestError(
                f"reference capacity exceeded ({MAX_REF_IMAGES} refs max): "
                "split the shot or reduce bound entities — references are never dropped"
            )
        slot = _r2v_count(manifest)
        manifest.append(
            {
                "slot_key": f"ref_image_{slot}",
                "prompt_tag": f"<Picture {slot + 1}>",
                "role": role if role != "character_identity" else "character_identity",
                "entity": entity,
                "artifact_id": b["artifact_id"],
                "sha256": b["sha256"],
                "asset_revision": b["asset_revision"],
            }
        )
    return manifest


def r2v_entries(manifest: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in manifest if m.get("role") != I2V_FIRST_FRAME_ROLE]


def _r2v_count(manifest: list[dict[str, Any]]) -> int:
    return len(r2v_entries(manifest))


def validate_manifest_against_capacity(manifest: list[dict[str, Any]]) -> None:
    if not manifest or manifest[0].get("role") != STORY_ROLE:
        raise ReferenceManifestError("first reference must be the story composition")
    if sum(m.get("role") == STORY_ROLE for m in manifest) != 1:
        raise ReferenceManifestError("exactly one story composition required")
    if sum(m.get("role") == I2V_FIRST_FRAME_ROLE for m in manifest) > 1:
        raise ReferenceManifestError("at most one I2V first-frame composition")
    if sum(m.get("role") == R2V_COMPOSITION_ROLE for m in manifest) > 1:
        raise ReferenceManifestError("at most one R2V composition reference")
    if any(m.get("role") == I2V_FIRST_FRAME_ROLE for m in manifest) and any(
        m.get("role") == R2V_COMPOSITION_ROLE for m in manifest
    ):
        raise ReferenceManifestError(
            "composition roles are exclusive; never relabel one as the other"
        )
    refs = r2v_entries(manifest)
    for i, m in enumerate(refs):
        if m.get("slot_key") != f"ref_image_{i}" or m.get("prompt_tag") != f"<Picture {i + 1}>":
            raise ReferenceManifestError("reference slot/tag order mismatch")
        if m.get("role") not in R2V_ROLES:
            raise ReferenceManifestError(f"unknown R2V role {m.get('role')}")
    if len(refs) > MAX_REF_IMAGES:
        raise ReferenceManifestError(f"{len(refs)} refs > capacity {MAX_REF_IMAGES}")
    slots = [m["slot_key"] for m in refs]
    if len(slots) != len(set(slots)):
        raise ReferenceManifestError("duplicate slot keys in manifest")
    for m in manifest:
        if not m.get("sha256") or not m.get("artifact_id"):
            raise ReferenceManifestError(f"entry missing hash/artifact: {m}")
        if m.get("role") == I2V_FIRST_FRAME_ROLE and m.get("slot_key") != "first_frame":
            raise ReferenceManifestError("I2V first frame must use slot_key first_frame")


def compile_prompt_with_tags(base_prompt: str, manifest: list[dict[str, Any]]) -> str:
    """Assert every R2V manifest <Picture i> tag appears in the prompt."""
    refs = r2v_entries(manifest)
    for m in refs:
        tag = m["prompt_tag"]
        if tag not in base_prompt:
            raise ReferenceManifestError(
                f"prompt missing {tag} ({m['role']}) — installed node validates "
                "strict prompt tags and would reject this generation"
            )
    expected = {m["prompt_tag"] for m in refs}
    if set(re.findall(r"<Picture \d+>", base_prompt)) != expected:
        raise ReferenceManifestError("prompt contains an unconnected picture tag")
    return base_prompt


__all__ = [
    "APPEARANCE_ROLE",
    "I2V_FIRST_FRAME_ROLE",
    "MAX_REF_AUDIOS",
    "MAX_REF_IMAGES",
    "MAX_REF_VIDEOS",
    "R2V_COMPOSITION_ROLE",
    "ReferenceManifestError",
    "STORY_ROLE",
    "build_reference_manifest",
    "compile_prompt_with_tags",
    "r2v_entries",
    "validate_manifest_against_capacity",
]
