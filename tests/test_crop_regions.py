"""Region proposals preserve interiors; synthetic pages, no model or network."""

import cv2
import numpy as np
from PIL import Image

from koma.crops import export_page
from koma.models.domain import BBox, Panel
from koma.steps.crop_proposals import _panel_region, propose_masks, rebuild_panel_regions


def test_gap_repair_fills_white_skin_and_screentone_interiors(tmp_path):
    pixels = np.full((100, 160, 3), 255, dtype=np.uint8)
    cv2.rectangle(pixels, (5, 5), (74, 94), (0, 0, 0), 2)
    pixels[4:7, 35:37] = 255  # small break in otherwise closed panel border
    pixels[25:45, 20:40] = (245, 210, 185)  # light interior content
    pixels[55:70, 20:40] = 190  # screentone
    cv2.rectangle(pixels, (85, 5), (154, 94), (0, 0, 0), 2)
    pixels[4:7, 105:135] = 255  # large open border: preserve extent, flag it
    page = tmp_path / "page.png"
    Image.fromarray(pixels).save(page)
    annotation = propose_masks(page, [
        Panel(panel_id="left", bbox=BBox(x=5, y=5, w=70, h=90), order=2),
        Panel(panel_id="right", bbox=BBox(x=85, y=5, w=70, h=90), order=1),
    ])
    masks = annotation.ownership()
    assert masks["left"][10:90, 10:70].all()
    assert masks["right"][10:90, 90:150].all()
    assert "Unclosed" in annotation.panels[1].rationale
    assert not (masks["left"] & masks["right"]).any()
    assert annotation.needs_review
    entries = export_page(page, annotation, tmp_path / "exports")
    crop = np.array(Image.open(tmp_path / "exports" / entries["left"]["crop_path"]))
    assert (crop[20:40, 15:35, :3] == pixels[25:45, 20:40]).all()
    assert (crop[20:40, 15:35, 3] == 255).all()


def test_closed_diagonal_region_preserves_nonrectangular_geometry():
    gray = np.full((100, 100), 255, dtype=np.uint8)
    cv2.polylines(gray, [np.array([[3, 3], [96, 3], [3, 96]])], True, 0, 2)
    region, reason = _panel_region(gray)
    assert region[20:40, 20:40].all()
    assert not region[80:95, 80:95].any()
    assert "Closed-border" in reason


def test_unclosed_linework_never_becomes_shards():
    gray = np.full((100, 100), 255, dtype=np.uint8)
    cv2.line(gray, (0, 0), (99, 99), 0, 3)
    cv2.line(gray, (0, 99), (99, 0), 0, 3)
    region, reason = _panel_region(gray)
    assert region.all()
    assert "correction required" in reason


def test_overlapping_region_proposals_have_exclusive_interiors(tmp_path):
    page = tmp_path / "page.png"
    Image.new("RGB", (100, 100), "white").save(page)
    result = propose_masks(page, [
        Panel(panel_id="small", bbox=BBox(x=10, y=10, w=30, h=30)),
        Panel(panel_id="large", bbox=BBox(x=0, y=0, w=100, h=100)),
    ])
    masks = result.ownership()
    assert masks["small"].sum() == 900
    assert masks["large"].sum() == 9100
    assert not (masks["small"] & masks["large"]).any()
    # Rebuilding keeps initial evidence and reopens boundary review.
    old_bytes = result.canonical_bytes()
    rebuilt = rebuild_panel_regions(page, result)
    assert result.canonical_bytes() == old_bytes
    assert rebuilt.proposals["large"] == result.proposals["large"]
    assert rebuilt.needs_review
