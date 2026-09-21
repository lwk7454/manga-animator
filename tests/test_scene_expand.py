"""Tests for the scene-crop + expand fixes (G4 scene evidence + G5 expand)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from koma.delivery import fit_contain_box
from koma.scenes import scene_bbox, scene_crop


def test_scene_bbox_union_with_margin():
    geometry = {
        "p1": {"bbox_x": 554, "bbox_y": 159, "bbox_w": 613, "bbox_h": 818,
               "page_w": 1280, "page_h": 2048},
        "p2": {"bbox_x": 139, "bbox_y": 1196, "bbox_w": 731, "bbox_h": 827,
               "page_w": 1280, "page_h": 2048},
    }
    box = scene_bbox(["p1", "p2"], geometry)
    # union: x 554..1167 ∪ 139..870 → 139..1167; y 159..977 ∪ 1196..2023
    # margin = 2% of union size (1028x1864) ≈ 20/37 per axis
    assert box["x"] <= 139 and box["w"] >= 1167 - 139
    assert box["y"] <= 159 and box["y"] >= 159 - 40
    assert box["y"] + box["h"] >= 2023


def test_scene_bbox_requires_geometry():
    import pytest

    with pytest.raises(ValueError):
        scene_bbox(["p1"], {})


def test_scene_crop_writes_union(tmp_path: Path):
    import cv2

    page = np.full((400, 300, 3), 255, dtype=np.uint8)
    # two "panels": left strip and right block
    cv2.rectangle(page, (10, 20), (110, 220), 255, -1)
    cv2.rectangle(page, (150, 60), (290, 300), 255, -1)
    page_path = Path("/tmp/koma-qual/test_scene_page.png")
    cv2.imwrite(str(page_path), page)
    geometry = {
        "a": {"bbox_x": 10, "bbox_y": 20, "bbox_w": 100, "bbox_h": 180,
              "page_w": 400, "page_h": 300},
        "b": {"bbox_x": 150, "bbox_y": 60, "bbox_w": 140, "bbox_h": 240,
              "page_w": 400, "page_h": 400},
    }
    dst = Path("/tmp/koma-qual/test_scene_crop.png")
    box = scene_crop(page_path, ["a", "b"], geometry, dst)
    img = cv2.imread(str(dst))
    assert img is not None
    # union + margin covers both boxes
    assert box["w"] >= 280 - 10 and box["h"] >= 280 - 10


def test_fit_contain_keeps_everything():
    # 3:4 source into 9:16 target: fit INSIDE (no crop), letterbox the rest.
    # scale = min(480/480, 864/640) = 1.0 → full 480x640 stays, letterboxed.
    x, y, w, h = fit_contain_box(480, 640, 480, 864)
    assert (w, h) == (480, 640)
    assert (x, y) == (0, 112)


def test_panels_index_written(tmp_path: Path):
    from koma.steps.ingest import _write_panels_index
    from koma.models.domain import BBox, Panel

    from koma.models.domain import PanelState

    panels = [
        Panel(panel_id="ch01_p001_01", bbox=BBox(x=5, y=6, w=100, h=200),
              order=1, state=PanelState.DETECTED),
        Panel(panel_id="ch01_p001_02", bbox=BBox(x=5, y=120, w=100, h=150),
              order=2, state=PanelState.DETECTED),
    ]
    panels_dir = tmp_path / "panels"
    panels_dir.mkdir()
    _write_panels_index(Path("p001.webp"), panels, panels_dir,
                        width=800, height=1200)
    index = json.loads((panels_dir / "panels_index.json").read_text())
    assert index["p001.webp"]["ch01_p001_01"]["bbox_w"] == 100
    assert index["_pages"]["p001.webp"] == {"page_w": 800, "page_h": 1200}
    # idempotent: rewrite with same data → same content
    _write_panels_index(Path("p001.webp"), panels, panels_dir,
                        width=800, height=1200)
    assert json.loads((panels_dir / "panels_index.json").read_text()) == index
