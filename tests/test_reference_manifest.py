"""R3 — reference manifest tests: capacity, slot order, strict tags, overflow."""

from __future__ import annotations

import pytest

from koma.adapters.reference_manifest import (
    ReferenceManifestError,
    build_reference_manifest,
    compile_prompt_with_tags,
    validate_manifest_against_capacity,
)


def _b(asset_id, revision, artifact="art:1", sha="sha:1"):
    return {"asset_id": asset_id, "asset_revision": revision,
            "artifact_id": artifact, "sha256": sha}


def test_slot_order_deterministic_panel_first():
    m = build_reference_manifest(
        panel_artifact_id="art:panel", panel_sha256="sha:panel",
        character_bindings=[_b("asset:b", 1), _b("asset:a", 2)])
    assert m[0]["prompt_tag"] == "<Picture 1>"
    assert m[0]["role"] == "story_composition"
    # characters sorted by (asset_id, revision) regardless of input order
    assert m[1]["entity"] == "asset:a" and m[1]["prompt_tag"] == "<Picture 2>"
    assert m[2]["entity"] == "asset:b" and m[2]["prompt_tag"] == "<Picture 3>"
    assert m[1]["slot_key"] == "ref_image_1"


def test_capacity_overflow_raises_never_drops():
    chars = [_b(f"asset:{i:02d}", 1) for i in range(12)]
    with pytest.raises(ReferenceManifestError) as ei:
        build_reference_manifest(
            panel_artifact_id="art:p", panel_sha256="sha:p",
            character_bindings=chars)
    assert "never dropped" in str(ei.value)


def test_exact_capacity_nine_is_allowed():
    chars = [_b(f"asset:{i:02d}", 1) for i in range(8)]  # panel + 8 = 9 total
    m = build_reference_manifest(
        panel_artifact_id="art:p", panel_sha256="sha:p",
        character_bindings=chars)
    assert len(m) == 9
    validate_manifest_against_capacity(m)


def test_validate_rejects_duplicates_and_missing_hashes():
    m = build_reference_manifest(
        panel_artifact_id="art:p", panel_sha256="sha:p",
        character_bindings=[_b("asset:a", 1)])
    m.append(dict(m[-1]))  # duplicate slot
    with pytest.raises(ReferenceManifestError):
        validate_manifest_against_capacity(m)
    m2 = build_reference_manifest(
        panel_artifact_id="art:p", panel_sha256="sha:p",
        character_bindings=[{"asset_id": "a", "asset_revision": 1,
                             "artifact_id": "art:x", "sha256": ""}])
    with pytest.raises(ReferenceManifestError):
        validate_manifest_against_capacity(m2)


def test_prompt_must_reference_every_tag():
    m = build_reference_manifest(
        panel_artifact_id="art:p", panel_sha256="sha:p",
        character_bindings=[_b("asset:a", 1)])
    good = "Use <Picture 1> for composition and <Picture 2> for identity."
    assert compile_prompt_with_tags(good, m) == good
    with pytest.raises(ReferenceManifestError) as ei:
        compile_prompt_with_tags("Use <Picture 1> only.", m)
    assert "<Picture 2>" in str(ei.value)


def test_roles_split_characters_objects_backgrounds():
    m = build_reference_manifest(
        panel_artifact_id="art:p", panel_sha256="sha:p",
        character_bindings=[_b("asset:c", 1)],
        object_bindings=[_b("asset:o", 1)],
        background_bindings=[_b("asset:bg", 1)])
    roles = [x["role"] for x in m]
    assert roles == ["story_composition", "character_identity",
                     "object_identity", "background_identity"]
