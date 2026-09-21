"""G4 — group fragment panels into a scene; primary = most complete crop.

Scene-level evidence (owner fix, 21 Sep): single-panel segmenter crops are
too narrow — a scene spanning several panels loses its context when only
one panel's crop is used. A shot built from a panel group gets a SCENE
crop: the union of its panels' bboxes on the source page (plus margin),
so the shot's evidence image carries the whole staged action.
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
    """Union of the group's panel bboxes with a small page-context margin.

    geometry entries: bbox_x/bbox_y/bbox_w/bbox_h (page coordinates, from
    ingest). The margin admits neighbouring speech-tails and gutters so the
    scene crop reads as one staged image.
    """
    boxes = [geometry[pid] for pid in panel_ids if pid in geometry]
    if not boxes:
        raise ValueError("scene crop requires panel geometry for every source panel")
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
    page_path, panel_ids: list[str], geometry: dict[str, dict[str, int]], dst_path
) -> dict[str, int]:
    """Write the group's scene crop from the source page; returns the bbox."""
    import cv2

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
