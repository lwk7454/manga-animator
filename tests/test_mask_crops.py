"""Synthetic-only regression coverage for pixel ownership, replay and approval."""

from __future__ import annotations

import json
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image

from koma.application.crops import edit_checkout, replay_page, submit_edit
from koma.crops import Annotation, Component, CropPanel, Mask, digest, export_page, read_mask
from koma.scenes import scene_bbox, scene_crop
from koma.storage.crop_store import CropStore, crop_blockers
from koma.storage.domain_store import KomaStore
from koma.storage.errors import BlockedPrerequisite, RevisionConflict


@pytest.fixture
def annotated(tmp_path):
    # Every pixel has a distinctive color; alpha is nontrivial too.
    pixels = np.arange(12 * 16 * 4, dtype=np.uint8).reshape(12, 16, 4)
    pixels[:, :, 3] = 200
    path = tmp_path / "page.png"
    Image.fromarray(pixels).save(path)
    left = np.zeros((12, 16), bool)
    left[1:11, 1:7] = True
    right = np.zeros_like(left)
    right[1:11, 9:15] = True
    # Bubble sits wholly inside LEFT rectangle, belongs to RIGHT panel.
    bubble = np.zeros_like(left)
    bubble[3:5, 2:5] = True
    bubble[5, 4:6] = True  # tail
    # Fine hair/SFX overfill spans gutter without redrawing.
    right[6, 7:10] = True
    annotation = Annotation(
        source_sha256=digest(path.read_bytes()), width=16, height=12,
        panels=[CropPanel(id="left", mask=Mask.encode(left)),
                CropPanel(id="right", mask=Mask.encode(right))],
        components=[Component(id="bubble", mask=Mask.encode(bubble), owner_panel="right",
                              candidates=["left", "right"], rationale="tail points right")],
        reading_order=["right", "left"],
    )
    return path, pixels, annotation


def test_pixel_exact_disjoint_transfers_and_byte_replay(annotated, tmp_path):
    path, pixels, annotation = annotated
    out = tmp_path / "out"
    entries = export_page(path, annotation, out)
    before = {str(p): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    masks = annotation.ownership()
    assert not (masks["left"] & masks["right"]).any()
    assert masks["right"][3, 2] and not masks["left"][3, 2]
    for pid, entry in entries.items():
        assert (out / entry["crop_path"]).name.startswith(f"{entry['order']:03d}_")
        crop = np.array(Image.open(out / entry["crop_path"]))
        x, y, w, h = (entry[f"bbox_{k}"] for k in ("x", "y", "w", "h"))
        selected = masks[pid][y:y+h, x:x+w]
        assert np.array_equal(crop[selected], pixels[y:y+h, x:x+w][selected])
        assert (crop[~selected] == 0).all()
        assert np.array_equal(read_mask(out / entry["mask_path"], 16, 12,
                                        entry["ownership_hash"]), masks[pid])
    assert export_page(path, annotation, out) == entries
    assert before == {str(p): p.read_bytes() for p in out.rglob("*") if p.is_file()}


def test_scene_union_excludes_other_panel_and_gutter(annotated, tmp_path):
    path, pixels, annotation = annotated
    entries = export_page(path, annotation, tmp_path / "panels")
    dst = tmp_path / "scene.png"
    box = scene_crop(path, ["right"], entries, dst, masks_root=tmp_path / "panels")
    crop = np.array(Image.open(dst))
    assert crop[3-box["y"], 2-box["x"], 3] == 200  # transferred bubble
    assert crop[2-box["y"], 3-box["x"], 3] == 0  # neighbour hidden
    box = scene_crop(path, ["right", "left"], entries, dst,
                     masks_root=tmp_path / "panels")
    crop = np.array(Image.open(dst))
    assert crop[2-box["y"], 8-box["x"], 3] == 0  # gutter stays transparent
    assert np.array_equal(crop[6-box["y"], 8-box["x"]], pixels[6, 8])  # overfill


@pytest.mark.parametrize("runs", [[(-1, 1)], [(0, 0)], [(191, 2)], [(4, 3), (6, 1)]])
def test_invalid_mask_runs_fail(runs):
    with pytest.raises(ValueError):
        Mask(runs=runs).array(16, 12)


@pytest.mark.parametrize("change", ["owner", "order", "path", "unowned", "duplicate"])
def test_invalid_annotation_ids_and_ownership(annotated, change):
    _, _, a = annotated
    data = a.model_dump(mode="json")
    if change == "owner":
        data["components"][0]["owner_panel"] = "unknown"
    elif change == "order":
        data["reading_order"] = ["left", "left"]
    elif change == "path":
        data["panels"][0]["id"] = "../escape"
    elif change == "unowned":
        data["components"][0].update(owner_panel=None, needs_review=False)
    else:
        data["components"][0]["id"] = "left"
    with pytest.raises(ValueError):
        Annotation.model_validate_json(json.dumps(data))


def test_overlap_and_source_changes_fail_before_writing(annotated, tmp_path):
    path, _, a = annotated
    a.panels[1].mask = a.panels[0].mask
    with pytest.raises(ValueError, match="overlap"):
        export_page(path, a, tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="source hash"):
        export_page(path, a, tmp_path / "invalid")


def test_missing_geometry_and_corrupt_mask_do_not_fall_back(annotated, tmp_path):
    path, _, a = annotated
    entries = export_page(path, a, tmp_path)
    with pytest.raises(ValueError):
        scene_bbox(["right", "missing"], entries)
    (tmp_path / entries["right"]["mask_path"]).unlink()
    with pytest.raises(FileNotFoundError):
        scene_crop(path, ["right"], entries, tmp_path / "scene.png", masks_root=tmp_path)


@pytest.fixture
def crop_db(annotated, tmp_path):
    path, _, annotation = annotated
    data = tmp_path / "data"
    root = data / "series" / "sample" / "source"
    root.mkdir(parents=True)
    (root / path.name).write_bytes(path.read_bytes())
    store = KomaStore.open(data)
    store.create_series("sample")
    store.ensure_chapter("sample", 1)
    result = store.import_pages("sample", 1, [{
        "source_path": path.name, "sha256": annotation.source_sha256, "width": 16,
        "height": 12, "panels": [
            {"panel_id": p.id, "bbox": {"x": 0, "y": 0, "w": 16, "h": 12}}
            for p in annotation.panels],
    }], operation_id="test", import_source_id="test")
    page_id = result["created"][0]
    CropStore(store.db).submit(page_id, annotation, expected_revision=0)
    yield store, page_id, annotation
    store.db.close()


def clear_flags(annotation):
    annotation.order_needs_review = False
    for item in [*annotation.panels, *annotation.components]:
        item.needs_review = False


def test_flags_and_revision_fences_are_required(crop_db):
    store, page, annotation = crop_db
    crops = CropStore(store.db)
    with pytest.raises(BlockedPrerequisite):
        crops.approve(page, expected_revision=1, actor="reviewer")
    with pytest.raises(BlockedPrerequisite):
        crops.require_approved(["left"])
    clear_flags(annotation)
    assert crops.submit(page, annotation, expected_revision=1) == 2
    with pytest.raises(RevisionConflict):
        crops.submit(page, annotation, expected_revision=1)
    with pytest.raises(RevisionConflict):
        crops.approve(page, expected_revision=1, actor="reviewer")
    with pytest.raises(ValueError):
        crops.approve(page, expected_revision=2, actor="")
    # Clearing JSON flags alone did NOT approve anything.
    with pytest.raises(BlockedPrerequisite):
        crops.require_approved(["left"])
    crops.approve(page, expected_revision=2, actor="reviewer")
    crops.require_approved(["left", "right"])
    assert crops.submit(page, annotation, expected_revision=2) == 2
    assert crops.latest(page)["decision_status"] == "approved"
    annotation.reading_order.reverse()
    assert crops.submit(page, annotation, expected_revision=2) == 3
    with pytest.raises(BlockedPrerequisite):
        crops.require_approved(["left"])
    with store.db.transaction() as conn:
        assert len(crop_blockers(conn, ["left", "right"])) == 2
        assert conn.execute("SELECT COUNT(*) FROM crop_annotations").fetchone()[0] == 3


def test_preview_not_registered_approved_rgba_is_registered(crop_db):
    store, page, annotation = crop_db
    replay_page(store, "sample", 1, page)
    with store.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
    clear_flags(annotation)
    crops = CropStore(store.db)
    crops.submit(page, annotation, expected_revision=1)
    crops.approve(page, expected_revision=2, actor="reviewer")
    replay_page(store, "sample", 1, page)
    replay_page(store, "sample", 1, page)
    with store.db.transaction() as conn:
        rows = conn.execute("SELECT * FROM artifacts").fetchall()
    assert len(rows) == 2
    for row in rows:
        assert row["kind"] == "panel" and row["validation_status"] == "validated"
        image = store.db.data_dir / row["relative_path"]
        assert digest(image.read_bytes()) == row["sha256"]
        assert Image.open(image).mode == "RGBA"


def test_checkout_submit_roundtrip_and_no_silent_deletions(crop_db, tmp_path):
    store, page, annotation = crop_db
    edits = tmp_path / "edits"
    edit_checkout(store, page, edits)
    with pytest.raises(ValueError, match="empty"):
        edit_checkout(store, page, edits)
    assert submit_edit(store, page, edits, 1) == 1
    annotation.components = []
    with pytest.raises(ValueError, match="disappear"):
        CropStore(store.db).submit(page, annotation, expected_revision=1)
    annotation.dismissed["bubble"] = "Reviewed false positive light region"
    assert CropStore(store.db).submit(page, annotation, expected_revision=1) == 2


def test_cli_replay_uses_sqlite_not_edited_export(crop_db):
    store, page, _ = crop_db
    metadata = replay_page(store, "sample", 1, page)
    panels = store.db.data_dir / "series/sample/chapters/01/panels"
    annotation_path = panels / metadata["path"]
    original = annotation_path.read_bytes()
    annotation_path.write_text("invalid edited export")
    result = subprocess.run([
        sys.executable, "-m", "koma.steps.ingest", "replay", "sample", "--page", "1",
        "--data-dir", str(store.db.data_dir),
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert annotation_path.read_bytes() == original


def test_existing_shot_gate_blocks_pending_and_outdated_crops(crop_db):
    from koma.application.compile_work import panel_artifact
    from koma.storage.shot_store import ShotStore

    store, page, annotation = crop_db
    shots = ShotStore(store.db)
    series = store.require_series("sample")["id"]
    chapter = store.get_chapter("sample", 1)["id"]
    beat = shots.add_beat(series, chapter, 1, beat_index=1, source_panel_ids=["right"])
    shot = shots.add_shot_revision(series, chapter, beat_coverage=[beat["beat_id"]],
                                  source_panel_ids=["right"])["shot_revision_id"]
    shots.add_ratio_spec(shot, "16:9", target_width=160, target_height=90)
    with pytest.raises(BlockedPrerequisite, match="crop masks"):
        shots.require_production_ready(shot, "16:9")
    with pytest.raises(BlockedPrerequisite):
        panel_artifact(store, "right")
    crops = CropStore(store.db)
    clear_flags(annotation)
    crops.submit(page, annotation, expected_revision=1)
    crops.approve(page, expected_revision=2, actor="reviewer")
    replay_page(store, "sample", 1, page)
    artifact = panel_artifact(store, "right")
    with store.db.transaction() as conn:
        conn.execute("UPDATE shot_ratio_specs SET reference_manifest=? WHERE shot_revision_id=?",
                     (json.dumps([{"artifact_id": artifact["id"], "role": "story_composition"}]),
                      shot))
    assert shots.require_production_ready(shot, "16:9")["ready"]
    annotation.reading_order.reverse()
    crops.submit(page, annotation, expected_revision=2)
    crops.approve(page, expected_revision=3, actor="reviewer")
    replay_page(store, "sample", 1, page)
    assert panel_artifact(store, "right")["id"] != artifact["id"]
    with pytest.raises(BlockedPrerequisite, match="outdated crop"):
        shots.require_production_ready(shot, "16:9")


def test_pending_crop_cannot_be_order_certified(tmp_path):
    from koma.models.domain import ReadingDirection
    from koma.review.sheets import _certify_by_ids
    from koma.steps.ingest import import_chapter
    from koma.storage.series_store import SeriesStore
    from tests.helpers import PANELS_GRID, draw_page

    store = SeriesStore(tmp_path / "data")
    store.create_series("sample", direction=ReadingDirection.RTL)
    path = tmp_path / "page.png"
    draw_page(path, PANELS_GRID)
    page = import_chapter(store, "sample", 1, path)[0]
    with pytest.raises(BlockedPrerequisite, match="crop masks"):
        _certify_by_ids(store, "sample", 1, [p.panel_id for p in page.panels])
