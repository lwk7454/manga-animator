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


class ReferenceManifestError(ValueError):
    pass


def build_reference_manifest(
    *,
    panel_artifact_id: str,
    panel_sha256: str,
    character_bindings: list[dict[str, Any]],
    object_bindings: list[dict[str, Any]] | None = None,
    background_bindings: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Slot order is deterministic: Picture 1 = panel, then characters in
    stable (asset_id, revision) order, then objects, then backgrounds.

    Each entry: {slot_key, prompt_tag, role, entity, artifact_id, sha256}.
    prompt_tag is ONE-BASED; Picture 1 is the first panel reference.
    """
    # Direct-panel route (D14): the panel is the FIRST REFERENCE
    # (ref_image_0, prompt tag <Picture 1>) — no edit_image slot. The panel
    # is a soft story/composition reference, not a locked first frame.
    manifest: list[dict[str, Any]] = [
        {
            "slot_key": "ref_image_0",
            "prompt_tag": "<Picture 1>",
            "role": "story_composition",
            "entity": None,
            "artifact_id": panel_artifact_id,
            "sha256": panel_sha256,
        }
    ]
    ordered: list[tuple[str, str, dict[str, Any]]] = []
    for role, bindings in (
        ("character", character_bindings),
        ("object", object_bindings or []),
        ("background", background_bindings or []),
    ):
        for b in sorted(bindings, key=lambda x: (x["asset_id"], x["asset_revision"])):
            ordered.append((role, f"{b['asset_id']}", b))
    for role, entity, b in ordered:
        if len(manifest) >= MAX_REF_IMAGES:
            raise ReferenceManifestError(
                f"reference capacity exceeded ({MAX_REF_IMAGES} refs max): "
                "split the shot or reduce bound entities — references are never dropped"
            )
        manifest.append(
            {
                "slot_key": f"ref_image_{len(manifest)}",
                "prompt_tag": f"<Picture {len(manifest) + 1}>",
                "role": f"{role}_identity",
                "entity": entity,
                "artifact_id": b["artifact_id"],
                "sha256": b["sha256"],
                "asset_revision": b["asset_revision"],
            }
        )
    return manifest


def validate_manifest_against_capacity(manifest: list[dict[str, Any]]) -> None:
    if not manifest or manifest[0].get("role") != "story_composition":
        raise ReferenceManifestError("first reference must be the story composition")
    if sum(m.get("role") == "story_composition" for m in manifest) != 1:
        raise ReferenceManifestError("exactly one story composition required")
    for i, m in enumerate(manifest):
        if m.get("slot_key") != f"ref_image_{i}" or m.get("prompt_tag") != f"<Picture {i + 1}>":
            raise ReferenceManifestError("reference slot/tag order mismatch")
    if len(manifest) > MAX_REF_IMAGES:
        raise ReferenceManifestError(f"{len(manifest)} refs > capacity {MAX_REF_IMAGES}")
    slots = [m["slot_key"] for m in manifest]
    if len(slots) != len(set(slots)):
        raise ReferenceManifestError("duplicate slot keys in manifest")
    for m in manifest:
        if not m.get("sha256") or not m.get("artifact_id"):
            raise ReferenceManifestError(f"entry missing hash/artifact: {m}")


def compile_prompt_with_tags(base_prompt: str, manifest: list[dict[str, Any]]) -> str:
    """Assert every manifest <Picture i> tag appears in the prompt; the strict
    tag validator on the installed node rejects unconnected tags at runtime."""
    for m in manifest:
        tag = m["prompt_tag"]
        if tag not in base_prompt:
            raise ReferenceManifestError(
                f"prompt missing {tag} ({m['role']}) — installed node validates "
                "strict prompt tags and would reject this generation"
            )
    expected = {m["prompt_tag"] for m in manifest}
    if set(re.findall(r"<Picture \d+>", base_prompt)) != expected:
        raise ReferenceManifestError("prompt contains an unconnected picture tag")
    return base_prompt


__all__ = [
    "MAX_REF_AUDIOS",
    "MAX_REF_IMAGES",
    "MAX_REF_VIDEOS",
    "ReferenceManifestError",
    "build_reference_manifest",
    "compile_prompt_with_tags",
    "validate_manifest_against_capacity",
]
