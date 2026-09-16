"""P09 offline — full-source fit/pad/mask preprocessing + exact frame plans.

§16.1: every ratio's canvas contains the ENTIRE source (no crop, no squash),
centered, exposure region masked. §16.2: rational-exact ceiling frame math
with grid snap-up, grid-max → segmentation blocker.
"""

from __future__ import annotations

import numpy as np
import pytest

from koma.gpu.fit import build_fit_payload, frame_plan_from_samples, plan_fit, verify_fit


def _marker_image(w: int, h: int) -> np.ndarray:
    """Distinct edge/corner markers (§16.1): thick enough to survive downscale."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    t = max(2, max(w, h) // 100)  # marker thickness scales with image
    img[:t, :] = (0, 0, 255)          # top edge red
    img[h - t:, :] = (255, 0, 0)      # bottom edge blue
    img[:, :t] = (0, 255, 0)          # left edge green
    img[:, w - t:] = (255, 0, 255)    # right edge magenta
    return img


def test_landscape_source_into_wide_canvas_covers_full():
    plan = plan_fit(1600, 900, "16:9", long_side=1024)
    assert plan.canvas_w == 1024
    # 1600x900 scales to fit; source fully inside
    assert plan.dst_w <= plan.canvas_w and plan.dst_h <= plan.canvas_h
    assert verify_fit(plan)["ok"]


def test_portrait_source_into_wide_canvas_never_cropped():
    plan = plan_fit(900, 1600, "16:9", long_side=1024)
    # portrait in landscape: width fits, height is the binding side
    assert plan.dst_h == plan.canvas_h or plan.dst_w == plan.canvas_w
    v = verify_fit(plan)
    assert v["ok"] and v["exposed_px"] > 0


def test_both_ratio_orientations_produce_valid_plans():
    for src in [(1654, 2339), (2339, 1654), (1000, 1000)]:
        for ratio in ("16:9", "9:16"):
            plan = plan_fit(*src, ratio, long_side=1024)
            assert verify_fit(plan)["ok"], (src, ratio)


def test_payload_keeps_all_edge_markers():
    """§16.1 edge-marker test: the fit payload preserves the full rectangle."""
    src = _marker_image(800, 1200)
    plan = plan_fit(800, 1200, "9:16", long_side=1024)
    payload = build_fit_payload(src, plan)
    img, mask = payload["image"], payload["mask"]
    x0, y0, dw, dh = plan.offset_x, plan.offset_y, plan.dst_w, plan.dst_h
    placed = img[y0:y0 + dh, x0:x0 + dw]
    t = max(1, int(8 * plan.scale))  # scaled marker thickness (≥1px)
    # all four source EDGES are present at the placement (no crop, no squash)
    assert placed[:t, :, 2].mean() > 100      # top edge red survived
    assert placed[dh - t:, :, 0].mean() > 100  # bottom edge blue survived
    assert placed[:, :t, 1].mean() > 100       # left edge green survived
    assert placed[:, dw - t:, 0].mean() > 100 and placed[:, dw - t:, 2].mean() > 100
    # mask: source region zero, exposed region 255
    assert mask[y0:y0 + dh, x0:x0 + dw].max() == 0
    assert mask.sum() // 255 == verify_fit(plan)["exposed_px"]


def test_mask_marks_exposed_region_only():
    src = _marker_image(1200, 800)
    plan = plan_fit(1200, 800, "16:9", long_side=1024)
    payload = build_fit_payload(src, plan)
    mask = payload["mask"]
    top = mask[: plan.offset_y, :]
    bottom = mask[plan.offset_y + plan.dst_h:, :]
    left = mask[:, : plan.offset_x]
    right = mask[:, plan.offset_x + plan.dst_w:]
    for band in (top, bottom, left, right):
        if band.size:  # zero-width bands are legitimately empty
            assert (band == 255).all()
    # at least one axis must expose new area for a ratio ≠ source aspect
    assert top.size or bottom.size or left.size or right.size


def test_fit_is_centered_and_deterministic():
    p1 = plan_fit(1234, 739, "16:9", long_side=1024)
    p2 = plan_fit(1234, 739, "16:9", long_side=1024)
    assert (p1.offset_x, p1.offset_y) == (p2.offset_x, p2.offset_y)
    assert abs(p1.offset_x - (p1.canvas_w - p1.dst_w) // 2) <= 1


def test_invalid_inputs_rejected():
    with pytest.raises(ValueError):
        plan_fit(0, 100, "16:9")
    with pytest.raises(ValueError):
        plan_fit(100, 100, "bogus")


# ── exact frame planning (§16.2) ─────────────────────────────────────────────


def test_frame_plan_ceiling_never_rounds_down():
    # 48000Hz, 1.0s exactly @ 24fps = 24 frames → grid 5+17n: 39
    p = frame_plan_from_samples(48000, 48000, 24, 1, 5, 17, 1000)
    assert p["frames"] == 39  # ceil=24 → snapped UP onto 5+17n
    # just past a frame boundary: 1 frame + 1 sample
    p2 = frame_plan_from_samples(48001, 48000, 24, 1, 5, 17, 1000)
    assert p2["frames"] >= p["frames"]


def test_frame_plan_respects_grid_minimum():
    p = frame_plan_from_samples(1, 48000, 24, 1, 5, 17, 1000)
    assert p["frames"] == 5  # never below grid min


def test_grid_maximum_requires_segmentation_not_truncation():
    # 60s of audio at 17n+5 max 173 (10s ceiling) → blocker, not silent cut
    with pytest.raises(ValueError, match="segmentation"):
        frame_plan_from_samples(60 * 48000, 48000, 24, 1, 5, 17, 173)


def test_zero_samples_gives_minimum_frames():
    p = frame_plan_from_samples(0, 48000, 24, 1, 5, 17, 1000)
    assert p["frames"] == 5


def test_invalid_frame_plan_args():
    with pytest.raises(ValueError):
        frame_plan_from_samples(-1, 48000, 24, 1, 5, 17, 100)
    with pytest.raises(ValueError):
        frame_plan_from_samples(100, 0, 24, 1, 5, 17, 100)
    with pytest.raises(ValueError):
        frame_plan_from_samples(100, 48000, 24, 1, 5, 0, 100)
