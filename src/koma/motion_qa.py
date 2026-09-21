"""Frame-difference time-series motion QA. Contact sheets are not evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def frame_diff_series(path: Path, *, max_frames: int = 400) -> dict[str, Any]:
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"unreadable video {path}")
    prev = None
    series: list[float] = []
    try:
        while len(series) < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
            if prev is not None:
                series.append(float(np.mean(np.abs(gray - prev))))
            prev = gray
    finally:
        cap.release()
    if not series:
        raise ValueError(f"video has fewer than two frames: {path}")
    mean = sum(series) / len(series)
    peak = max(series)
    trough = min(series)
    variation = (peak / trough) if trough > 1e-6 else float("inf")
    return {
        "n": len(series),
        "mean": mean,
        "peak": peak,
        "trough": trough,
        "variation": variation,
        "series": series,
    }


__all__ = ["frame_diff_series"]
