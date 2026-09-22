"""Conservative local OpenCV proposals, explicitly requiring review.

This does not claim automatic semantic segmentation. External segmentation
tools can submit the same Mask/Component schema without changing export.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..crops import Annotation, Component, CropPanel, Mask, digest
from ..models.domain import Panel


def propose_masks(page: Path, panels: list[Panel], *, direction: str = "rtl") -> Annotation:
    gray = cv2.imread(str(page), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"unreadable page {page}")
    height, width = gray.shape
    _, ink = cv2.threshold(gray, 120, 255, cv2.THRESH_BINARY_INV)
    dilated = cv2.dilate(ink, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    by_box = {}
    for contour in contours:
        box = cv2.boundingRect(contour)
        if box not in by_box or cv2.contourArea(contour) > cv2.contourArea(by_box[box]):
            by_box[box] = contour
    proposals = {}
    raw = {}
    for p in panels:
        b = p.bbox
        if b.x < 0 or b.y < 0 or b.x + b.w > width or b.y + b.h > height:
            raise ValueError("panel proposal lies outside source page")
        mask = np.zeros_like(gray)
        contour = by_box.get((b.x, b.y, b.w, b.h))
        if contour is not None:
            cv2.drawContours(mask, [contour], -1, 255, cv2.FILLED)
        else:
            mask[b.y : b.y + b.h, b.x : b.x + b.w] = 255
        raw[p.panel_id] = mask != 0
        proposals[p.panel_id] = Mask.encode(mask != 0)

    # Overlap adjudication is provisional: smaller proposals get precedence.
    # Preserve the raw masks above, and flag EVERY panel, including hair edges.
    owned = {}
    seen = np.zeros_like(gray, dtype=bool)
    for pid in sorted(raw, key=lambda k: (int(raw[k].sum()), k)):
        mask = raw[pid] & ~seen
        if not mask.any():
            raise ValueError(f"proposal {pid} has no exclusive pixels; edit proposals manually")
        owned[pid] = mask
        seen |= mask

    # Enclosed light regions are balloon candidates, NOT classified balloons.
    light = np.where(gray > 220, 255, 0).astype(np.uint8)
    light_contours, _ = cv2.findContours(light, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    components = []
    selected = np.zeros_like(seen)
    candidates = []
    for contour in light_contours:
        area = cv2.contourArea(contour)
        x, y, w, h = cv2.boundingRect(contour)
        perimeter = cv2.arcLength(contour, True)
        if (
            0.0005 * width * height < area < 0.04 * width * height
            and x > 0
            and y > 0
            and x + w < width
            and y + h < height
            and perimeter > 0
            and 4 * np.pi * area / perimeter**2 > 0.3
        ):
            candidates.append((y, x, -area, contour))
    for _, _, _, contour in sorted(candidates, key=lambda c: c[:3]):
        m = np.zeros_like(gray)
        cv2.drawContours(m, [contour], -1, 255, cv2.FILLED)
        mask = m != 0
        cid = f"bubble_candidate_{len(components) + 1:03d}"
        if np.any(selected & mask):
            # Nested contour is retained as evidence, not a second semantic object.
            proposals[f"nested_{len(proposals):03d}"] = Mask.encode(mask)
            continue
        selected |= mask
        proposals[cid] = Mask.encode(mask)
        scores = sorted(owned, key=lambda pid: (-int((owned[pid] & mask).sum()), pid))
        possible = [pid for pid in scores if (owned[pid] & mask).any()]
        components.append(
            Component(
                id=cid,
                mask=Mask.encode(mask),
                candidates=possible,
                owner_panel=possible[0] if possible else None,
                rationale="Unclassified light-region candidate; inspect outline, text and tail. "
                "Geometric owner is provisional, not semantic adjudication.",
            )
        )
    return Annotation(
        source_sha256=digest(page.read_bytes()),
        width=width,
        height=height,
        direction=direction,
        panels=[CropPanel(id=p.panel_id, mask=Mask.encode(owned[p.panel_id])) for p in panels],
        components=components,
        proposals=proposals,
        reading_order=[p.panel_id for p in sorted(panels, key=lambda p: p.order or 0)],
        detector=f"opencv-{cv2.__version__}-contour-proposals-v1",
    )
