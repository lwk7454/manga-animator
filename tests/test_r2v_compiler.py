"""R3 — multi-reference compiler tests (graph wiring, capacity, upload plan)."""

from __future__ import annotations

from pathlib import Path

import pytest

from koma.adapters.reference_manifest import (
    ReferenceManifestError,
    build_reference_manifest,
)
from koma.r2v_compiler import (
    CompilerError,
    compile_shot_graph,
    panel_artifact_key,
    reference_upload_plan,
)
from koma.r2v_probe import digest


@pytest.fixture()
def panel(tmp_path: Path) -> Path:
    p = tmp_path / "panel.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)  # minimal-ish payload for digest
    return p


def _m(slot=None, n=1):
    m = build_reference_manifest(
        panel_artifact_id="art:panel",
        panel_sha256="sha:panel",
        character_bindings=[
            {
                "asset_id": f"asset:{i}",
                "asset_revision": 1,
                "artifact_id": f"art:c{i}",
                "sha256": f"sha:c{i}",
            }
            for i in range(n)
        ],
    )
    return m


def test_panel_artifact_key_from_manifest():
    m = _m()
    assert panel_artifact_key(m) == "art:panel"
    with pytest.raises(ReferenceManifestError):
        panel_artifact_key([m for m in m if m["role"] != "story_composition"])


def test_compile_wires_panel_and_refs(tmp_path: Path):
    panel = tmp_path / "panel.png"
    panel.write_bytes(b"\x89PNG\r\n\x1a\n" + b"1" * 32)
    manifest = _m(n=2)
    manifest[0]["sha256"] = digest(panel)
    uploads = {"art:panel": "koma_panel.png", "art:c0": "koma_c0.png", "art:c1": "koma_c1.png"}
    graph = compile_shot_graph(
        variant="turbo4",
        panel_path=panel,
        reference_manifest=manifest,
        prompt="Use <Picture 1> for composition and <Picture 2> and <Picture 3> for identity.",
        width=864,
        height=480,
        frames=124,
        seed=42,
        upload_names=uploads,
    )
    assert graph["137"]["inputs"]["image"] == "koma_panel.png"
    assert "ref_images" not in graph["136"]["inputs"]
    for i, node in enumerate(["137", "Lref_image_1", "Lref_image_2"]):
        assert graph["136"]["inputs"][f"ref_images.ref_image_{i}"] == [node, 0]
    assert graph["136"]["inputs"]["width"] == 864
    assert graph["136"]["inputs"]["length"] == 124
    assert graph["129"]["inputs"]["noise_seed"] == 42
    # ref LoadImage nodes carry the uploaded names
    assert graph["Lref_image_1"]["inputs"]["image"] == "koma_c0.png"
    assert graph["Lref_image_2"]["inputs"]["image"] == "koma_c1.png"


def test_compile_rejects_unuploaded_panel(tmp_path: Path):
    panel = tmp_path / "panel.png"
    panel.write_bytes(b"\x89PNG\r\n\x1a\n" + b"1" * 32)
    with pytest.raises(CompilerError) as ei:
        compile_shot_graph(
            variant="turbo4",
            panel_path=panel,
            reference_manifest=[{**m, "sha256": digest(panel)} for m in _m(n=1)],
            prompt="<Picture 1> <Picture 2> move",
            width=864,
            height=480,
            frames=124,
            seed=1,
            upload_names={},
        )
    assert "not uploaded" in str(ei.value)


def test_upload_plan_unique_and_ordered():
    panel = Path("/tmp/whatever.png")
    m = build_reference_manifest(
        panel_artifact_id="art:panel",
        panel_sha256="sha:p",
        character_bindings=[_b("asset:c", 1)],
        object_bindings=[
            {
                "asset_id": "asset:o",
                "asset_revision": 1,
                "artifact_id": "art:panel",
                "sha256": "sha:1",
            }
        ],
    )
    # two entries sharing artifact id -> duplicate upload name error
    with pytest.raises(CompilerError):
        reference_upload_plan(m, panel)


def _b(asset_id, revision, artifact="art:1", sha="sha:1"):
    return {
        "asset_id": asset_id,
        "asset_revision": revision,
        "artifact_id": artifact,
        "sha256": sha,
    }


def test_capacity_respected_through_compile(tmp_path: Path):
    panel = tmp_path / "panel.png"
    panel.write_bytes(b"\x89PNG\r\n\x1a\n" + b"2" * 32)
    uploads = {"art:panel": "panel.png"}
    manifest = build_reference_manifest(
        panel_artifact_id="art:panel",
        panel_sha256="sha:panel",
        character_bindings=[_b(f"asset:{i}", 1, f"art:c{i}", f"sha:{i}") for i in range(8)],
    )  # panel + 8 = 9 capacity
    manifest[0]["sha256"] = digest(panel)
    for entry in manifest:
        uploads[entry["artifact_id"]] = f"u_{entry['artifact_id'].replace(':', '_')}.png"
    graph = compile_shot_graph(
        variant="turbo4",
        panel_path=panel,
        reference_manifest=manifest,
        prompt=" ".join(f"<Picture {i}>" for i in range(1, 10)),
        width=864,
        height=480,
        frames=124,
        seed=1,
        upload_names=uploads,
    )
    assert sum(k.startswith("ref_images.") for k in graph["136"]["inputs"]) == 9
    # one more ref overflows at manifest build time
    with pytest.raises(ReferenceManifestError):
        build_reference_manifest(
            panel_artifact_id="art:panel",
            panel_sha256="sha:panel",
            character_bindings=[_b(f"asset:{i}", 1) for i in range(10)],
        )
