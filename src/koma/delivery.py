"""Ratio delivery helpers. 9:16 of a 3:4 recolor is a center-crop (G5)."""

from __future__ import annotations

from pathlib import Path

from .storage.errors import InvalidTransition

RATIO_CANVAS = {"16:9": (864, 480), "9:16": (480, 864), "3:4": (480, 640)}


def center_crop_box(src_w: int, src_h: int, dst_w: int, dst_h: int) -> tuple[int, int, int, int]:
    if src_w <= 0 or src_h <= 0 or dst_w <= 0 or dst_h <= 0:
        raise InvalidTransition("crop dimensions must be positive")
    target_aspect = dst_w / dst_h
    src_aspect = src_w / src_h
    if src_aspect > target_aspect:
        crop_h = src_h
        crop_w = min(src_w, max(1, int(round(src_h * target_aspect))))
        return (src_w - crop_w) // 2, 0, crop_w, crop_h
    crop_w = src_w
    crop_h = min(src_h, max(1, int(round(src_w / target_aspect))))
    return 0, (src_h - crop_h) // 2, crop_w, crop_h


def center_crop_image(src_path: Path, dst_path: Path, *, ratio: str) -> dict[str, int]:
    import cv2

    if ratio not in RATIO_CANVAS:
        raise InvalidTransition(f"unsupported delivery ratio {ratio}")
    image = cv2.imread(str(src_path), cv2.IMREAD_COLOR)
    if image is None:
        raise InvalidTransition(f"unreadable source image {src_path}")
    src_h, src_w = image.shape[:2]
    dst_w, dst_h = RATIO_CANVAS[ratio]
    x, y, w, h = center_crop_box(src_w, src_h, dst_w, dst_h)
    resized = cv2.resize(image[y : y + h, x : x + w], (dst_w, dst_h), interpolation=cv2.INTER_AREA)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(dst_path), resized):
        raise InvalidTransition(f"failed to write crop {dst_path}")
    return {"x": x, "y": y, "w": w, "h": h, "out_w": dst_w, "out_h": dst_h}


def chroma_mean(path: Path) -> float:
    import cv2
    import numpy as np

    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise InvalidTransition(f"unreadable image {path}")
    ycrcb = cv2.cvtColor(image, cv2.COLOR_BGR2YCrCb)
    cr = ycrcb[:, :, 1].astype(np.float32)
    cb = ycrcb[:, :, 2].astype(np.float32)
    return float(((cr - 128.0) ** 2 + (cb - 128.0) ** 2).mean() ** 0.5)


__all__ = ["RATIO_CANVAS", "center_crop_box", "center_crop_image", "chroma_mean"]
