"""G4 — group fragment panels into a scene; primary = most complete crop.

Scene-level evidence (owner fix, 21 Sep): single-panel segmenter crops are
too narrow — a scene spanning several panels loses its context when only
one panel's crop is used. A shot built from a panel group gets a SCENE
crop: the union of its panels' owned pixels on the source page. Bounding
rectangles remain a compatibility path for pages without mask annotations.
"""

from __future__ import annotations


def pick_primary_panel(panel_ids: list[str], geometry: dict[str, dict[str, int]] | None) -> str:
    if not panel_ids:
        raise ValueError("shot requires at least one source panel")
    if not geometry:
        return panel_ids[0]

    def area(pid: str) -> tuple[int, int]:
        box = geometry.get(pid) or {}
        w = int(box.get("bbox_w") or box.get("w") or 0)
        h = int(box.get("bbox_h") or box.get("h") or 0)
        return (w * h, -panel_ids.index(pid))

    return max(panel_ids, key=area)


def order_source_panels(
    panel_ids: list[str], geometry: dict[str, dict[str, int]] | None
) -> list[str]:
    primary = pick_primary_panel(panel_ids, geometry)
    return [primary, *[pid for pid in panel_ids if pid != primary]]


def scene_bbox(
    panel_ids: list[str], geometry: dict[str, dict[str, int]], *, margin_pct: float = 0.02
) -> dict[str, int]:
    """Canvas bounds of the group's panel geometry, optionally with a margin.

    geometry entries: bbox_x/bbox_y/bbox_w/bbox_h (page coordinates, from
    ingest). Mask-mode scene_crop computes tight actual mask bounds and copies
    only owned pixels; this helper alone never establishes pixel ownership.
    """
    if not panel_ids or any(pid not in geometry for pid in panel_ids):
        raise ValueError("scene crop requires panel geometry for every source panel")
    boxes = [geometry[pid] for pid in panel_ids]
    x0 = min(int(b.get("bbox_x", 0)) for b in boxes)
    y0 = min(int(b.get("bbox_y", 0)) for b in boxes)
    x1 = max(int(b.get("bbox_x", 0)) + int(b.get("bbox_w", 0)) for b in boxes)
    y1 = max(int(b.get("bbox_y", 0)) + int(b.get("bbox_h", 0)) for b in boxes)
    page_w = max(int(b.get("page_w", 0)) for b in boxes)
    page_h = max(int(b.get("page_h", 0)) for b in boxes)
    mx = int((x1 - x0) * margin_pct)
    my = int((y1 - y0) * margin_pct)
    x0, y0 = max(0, x0 - mx), max(0, y0 - my)
    if page_w and page_h:
        x1, y1 = min(page_w, x1 + mx), min(page_h, y1 + my)
    else:
        x1, y1 = x1 + mx, y1 + my
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def scene_crop(
    page_path, panel_ids: list[str], geometry: dict[str, dict], dst_path,
    *, masks_root=None,
) -> dict[str, int]:
    """Union owned pixels, not bounding rectangles. Legacy bbox-only pages still work.

    Relative mask paths are resolved against masks_root (panels_index parent).
    Missing/corrupt masks on mask-enabled pages NEVER fall back to rectangles.
    Margins in mask mode are transparent, not neighbouring page content.
    """
    from pathlib import Path

    import cv2
    import numpy as np
    from PIL import Image

    from .crops import atomic_write, bounds, digest, masked_image, png_bytes, read_mask

    scene_bbox(panel_ids, geometry)  # fail on incomplete membership
    entries = [geometry[pid] for pid in panel_ids]
    if any(e.get("mask_path") for e in entries):
        if not all(e.get("mask_path") for e in entries):
            raise ValueError("cannot mix mask and rectangle-only panel geometry")
        page_path = Path(page_path)
        sha = digest(page_path.read_bytes())
        with Image.open(page_path) as im:
            pixels = np.array(im.convert("RGBA"))
            width, height = im.size
        union = np.zeros((height, width), dtype=bool)
        for entry in entries:
            if entry.get("source_sha256") != sha:
                raise ValueError("scene member belongs to a different source page")
            path = Path(entry["mask_path"])
            if not path.is_absolute():
                if masks_root is None:
                    raise ValueError("relative mask_path requires masks_root")
                root = Path(masks_root).resolve()
                path = (root / path).resolve()
                if not path.is_relative_to(root):
                    raise ValueError("mask_path escapes masks_root")
            mask = read_mask(path, width, height, entry["ownership_hash"])
            if np.any(union & mask):
                raise ValueError("scene member ownership masks overlap")
            union |= mask
        box = bounds(union)
        atomic_write(Path(dst_path), png_bytes(masked_image(pixels, union, box)))
        return box

    page = cv2.imread(str(page_path))
    if page is None:
        raise ValueError(f"unreadable source page {page_path}")
    box = scene_bbox(panel_ids, geometry)
    crop = page[box["y"] : box["y"] + box["h"], box["x"] : box["x"] + box["w"]]
    dst_path = __import__("pathlib").Path(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(dst_path), crop):
        raise ValueError(f"failed to write scene crop {dst_path}")
    return box


__all__ = ["order_source_panels", "pick_primary_panel", "scene_bbox", "scene_crop"]
