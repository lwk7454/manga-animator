"""G1 — colored R2V manifest roles and prompt contract."""

from __future__ import annotations

import pytest

from koma.adapters.reference_manifest import (
    APPEARANCE_ROLE,
    ReferenceManifestError,
    build_reference_manifest,
    compile_prompt_with_tags,
    validate_manifest_against_capacity,
)
from koma.r2v_prompt import assert_colored_prompt, build_colored_r2v_prompt


def _b(asset_id, revision=1, artifact="art:s", sha="sha:s"):
    return {
        "asset_id": asset_id,
        "asset_revision": revision,
        "artifact_id": artifact,
        "sha256": sha,
    }


def test_appearance_evidence_is_picture_two():
    manifest = build_reference_manifest(
        panel_artifact_id="art:panel",
        panel_sha256="sha:panel",
        character_bindings=[],
        appearance_bindings=[_b("asset:hero", artifact="art:sheet", sha="sha:sheet")],
    )
    assert manifest[0]["role"] == "story_composition"
    assert manifest[1]["role"] == APPEARANCE_ROLE
    assert manifest[1]["prompt_tag"] == "<Picture 2>"
    validate_manifest_against_capacity(manifest)


def test_colored_prompt_rejects_bw_preservation():
    with pytest.raises(ReferenceManifestError, match="PLAN line 75"):
        assert_colored_prompt("Animate <Picture 1> and preserve black and white lineart.")


def test_colored_prompt_requires_recolor_language():
    with pytest.raises(ReferenceManifestError, match="recolor"):
        assert_colored_prompt("Use <Picture 1> and <Picture 2> to animate the scene.")


def test_colored_prompt_wires_panel_and_sheet_tags():
    manifest = build_reference_manifest(
        panel_artifact_id="art:panel",
        panel_sha256="sha:panel",
        character_bindings=[],
        appearance_bindings=[_b("asset:hero", artifact="art:sheet", sha="sha:sheet")],
    )
    prompt = build_colored_r2v_prompt(
        manifest, caption="two characters in a close embrace; right hand visible"
    )
    assert "<Picture 1>" in prompt and "<Picture 2>" in prompt
    assert "recolor" in prompt.lower()
    assert "appearance evidence" in prompt.lower()
    compile_prompt_with_tags(prompt, manifest)
