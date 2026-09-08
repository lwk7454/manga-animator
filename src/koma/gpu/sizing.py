"""Frame-grid sizing — audio-first lengths snapped to the node's live grid.

Inherited doctrine (vidgen decisions #8/#30/#38, koma decision #032):
- clip length = its audio bed duration x fps, CEIL (never undershoot)
- the grid (min, step) is read LIVE from ``/object_info`` — never hardcoded
- never pad or speed-adjust video; surplus becomes audio-tail silence
"""

from __future__ import annotations

import math
from typing import Any

# Live-verified fallback (vidgen, Sep 2026): MiniMaxH3ImageToVideo length
# widget = min 5, step 17 → the 17n+5 grid. Used only if /object_info is
# unreachable; the live read always wins.
MINIMAX_H3_GRID = (5, 17)


def read_frame_grid(
    object_info: dict[str, Any], node_type: str, length_field: str = "length"
) -> tuple[int, int]:
    """Extract (min, step) from an ``/object_info[/<node>]`` payload."""
    node = object_info.get(node_type)
    if not node:
        raise ValueError(f"node type {node_type!r} not in object_info")
    input_spec = node.get("input", {}).get("required", {})
    spec = input_spec.get(length_field)
    if not spec or len(spec) < 2 or not isinstance(spec[1], dict):
        raise ValueError(f"{node_type}.{length_field} has no (default, [min, max, step…]) spec")
    meta = spec[1]
    lo = int(meta.get("min", 1))
    step = int(meta.get("step", 1))
    if step <= 0:
        raise ValueError(f"{node_type}.{length_field} step must be positive, got {step}")
    return lo, step


def align_frame_count(frames: int, grid: tuple[int, int]) -> int:
    """Snap a frame count UP onto the grid: ``min + n*step`` (never undershoot).

    The video node's core silently re-queues off-grid lengths as the next grid
    value anyway (vidgen live-verified) — snapping here keeps the submitted
    length and the audio lock math in agreement.
    """
    lo, step = grid
    if frames <= lo:
        return lo
    n = math.ceil((frames - lo) / step)
    return lo + n * step


def clip_frames(audio_duration_s: float, fps: int, grid: tuple[int, int]) -> int:
    """Audio-first sizing: duration x fps, CEIL, then snapped to the grid."""
    raw = math.ceil(max(0.0, audio_duration_s) * fps)
    return align_frame_count(raw, grid)


def clip_duration_s(frames: int, fps: int) -> float:
    """Rendered duration of a grid-aligned frame count."""
    return frames / fps
