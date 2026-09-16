"""Full-source keyframe preprocessing: fit/pad/mask per ratio (PLAN §16.1).

For each requested ratio, build a canvas that CONTAINS the entire source
image after uniform scaling, placed deterministically (centered), with the
newly exposed region identified by a mask. The outpaint graph consumes
(image, mask) at the requested canvas dimensions. A center crop is
PROHIBITED; a Pillow/cv2 resize+pad is valid PREPROCESSING but is never
presented as completed generative outpainting — that label belongs to the
GPU lane's output (§16.1, P09 live probe).

Canvas dims snap to the /16 diffusion constraint (short side) with the long
side hitting the profile's long_side, matching the #029 native-aspect
doctrine: the SOURCE CONTENT is never squashed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class FitPlan:
    """Deterministic placement of the source inside the ratio canvas."""

    canvas_w: int
    canvas_h: int
    scale: float
    src_w: int
    src_h: int
    dst_w: int
    dst_h: int
    offset_x: int
    offset_y: int

    @property
    def covers_full_source(self) -> bool:
        return self.dst_w == round(self.src_w * self.scale) and (
            self.dst_h == round(self.src_h * self.scale)
        )


def plan_fit(src_w: int, src_h: int, ratio: str, long_side: int = 1024,
             snap: int = 16) -> FitPlan:
    """Compute the full-source-fit placement for one ratio.

    Uniform scale so the ENTIRE source fits; the canvas is the ratio aspect
    at the profile's long side; the source rectangle is centered.
    """
    if src_w <= 0 or src_h <= 0:
        raise ValueError("source dimensions must be positive")
    rw, rh = (float(x) for x in ratio.split(":"))
    ratio_wh = rw / rh

    # canvas: ratio aspect, long side = long_side, short snapped to /16
    if ratio_wh >= 1.0:
        canvas_w = long_side
        canvas_h = max(snap, round(long_side / ratio_wh / snap) * snap)
    else:
        canvas_h = long_side
        canvas_w = max(snap, round(long_side * ratio_wh / snap) * snap)

    scale = min(canvas_w / src_w, canvas_h / src_h)
    dst_w = max(1, round(src_w * scale))
    dst_h = max(1, round(src_h * scale))
    offset_x = (canvas_w - dst_w) // 2
    offset_y = (canvas_h - dst_h) // 2
    plan = FitPlan(canvas_w, canvas_h, scale, src_w, src_h, dst_w, dst_h,
                   offset_x, offset_y)
    if dst_w > canvas_w or dst_h > canvas_h:
        raise AssertionError("fit plan overflows canvas — invalid scale math")
    return plan


def build_fit_payload(
    source_bgr: np.ndarray, plan: FitPlan,
) -> dict[str, Any]:
    """Render the preprocessing payload: padded image + exposure mask.

    The padded image contains the ENTIRE source (uniform scale, centered).
    The mask marks the newly exposed region (255 = to be generated).
    """
    import cv2

    src = source_bgr
    canvas = np.zeros((plan.canvas_h, plan.canvas_w, 3), dtype=np.uint8)
    mask = np.full((plan.canvas_h, plan.canvas_w), 255, dtype=np.uint8)
    resized = cv2.resize(src, (plan.dst_w, plan.dst_h), interpolation=cv2.INTER_AREA)
    x0, y0 = plan.offset_x, plan.offset_y
    canvas[y0:y0 + plan.dst_h, x0:x0 + plan.dst_w] = resized
    mask[y0:y0 + plan.dst_h, x0:x0 + plan.dst_w] = 0  # source region: keep
    return {"image": canvas, "mask": mask, "plan": plan}


def verify_fit(plan: FitPlan, tolerance: int = 1) -> dict:
    """Structural checks (§16.1 edge/corner-marker tests, plan-level).

    The live probe (GPU lane) must additionally demonstrate the source
    region reaching the model and new area generated; this verifies the
    deterministic math: full coverage, centering, mask geometry.
    """
    problems: list[str] = []
    if plan.dst_w > plan.canvas_w or plan.dst_h > plan.canvas_h:
        problems.append("source rectangle exceeds canvas (crop would occur)")
    if plan.offset_x < 0 or plan.offset_y < 0:
        problems.append("negative offsets (crop would occur)")
    expected_x = (plan.canvas_w - plan.dst_w) // 2
    expected_y = (plan.canvas_h - plan.dst_h) // 2
    if abs(plan.offset_x - expected_x) > tolerance or abs(plan.offset_y - expected_y) > tolerance:
        problems.append("source not centered")
    exposed = (plan.canvas_w * plan.canvas_h) - (plan.dst_w * plan.dst_h)
    if exposed <= 0 and (plan.canvas_w != plan.dst_w or plan.canvas_h != plan.dst_h):
        problems.append("no exposed region but canvas != source")
    return {"ok": not problems, "problems": problems, "exposed_px": exposed}


def frame_plan_from_samples(
    total_samples: int, sample_rate: int, fps_num: int, fps_den: int,
    grid_min: int, grid_step: int, grid_max: int,
) -> dict[str, Any]:
    """Exact audio-first frame plan (§16.2): rational ceiling, then grid snap.

    F = fps_num/fps_den; S = integer samples at rate R. Minimum frame count
    = ceil(S * F / R) computed in exact integer arithmetic, then rounded
    UP to the profile's valid frame grid (never down). Includes min/max and
    the selected grid in the plan artifact.
    """
    if total_samples < 0:
        raise ValueError("negative samples")
    if sample_rate <= 0 or fps_num <= 0 or fps_den <= 0:
        raise ValueError("invalid sample rate or fps")
    if grid_min <= 0 or grid_step <= 0 or grid_max < grid_min:
        raise ValueError("invalid frame grid")
    exact_num = total_samples * fps_num
    exact_den = (total_samples and (sample_rate * fps_den)) or (sample_rate * fps_den)
    if exact_num == 0:
        min_frames = grid_min
    else:
        # ceil(exact_num / exact_den) in integers
        min_frames = -(-exact_num // exact_den)
    min_frames = max(min_frames, grid_min)
    if min_frames > grid_max:
        raise ValueError(
            f"required {min_frames} frames exceeds grid maximum {grid_max} — "
            "reviewed segmentation or a longer qualified profile needed (§16.3)"
        )
    if min_frames <= grid_min:
        frames = grid_min
    else:
        n = -(-(min_frames - grid_min) // grid_step)
        frames = grid_min + n * grid_step
    frames = min(frames, grid_max)
    return {
        "frames": frames,
        "fps": {"num": fps_num, "den": fps_den},
        "grid": {"min": grid_min, "step": grid_step, "max": grid_max},
        "total_samples": total_samples,
        "sample_rate": sample_rate,
        "duration_samples": frames * exact_den // fps_num,
    }


__all__ = ["FitPlan", "build_fit_payload", "frame_plan_from_samples", "plan_fit", "verify_fit"]
