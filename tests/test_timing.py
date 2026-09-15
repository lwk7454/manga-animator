"""Durable feature tests; no live model services."""

from __future__ import annotations

import pytest

from koma.gpu.sizing import MINIMAX_H3_GRID, align_frame_count, clip_frames, read_frame_grid


class TestSizing:
    def test_read_grid_from_object_info(self):
        info = {
            "SomeNode": {
                "input": {"required": {"length": [56, {"min": 5, "max": 362, "step": 17}]}}
            }
        }
        assert read_frame_grid(info, "SomeNode") == (5, 17)

    def test_align_snaps_up(self):
        grid = (5, 17)
        assert align_frame_count(5, grid) == 5
        assert align_frame_count(6, grid) == 22
        assert align_frame_count(22, grid) == 22
        assert align_frame_count(23, grid) == 39

    def test_clip_frames_audio_first(self):
        # 4.67s * 24fps = 112.08 → ceil 113 → snap up to 124 (17n+5)
        assert clip_frames(4.666, 24, MINIMAX_H3_GRID) == 124

    def test_read_grid_missing_node_raises(self):
        with pytest.raises(ValueError):
            read_frame_grid({}, "Nope")


def test_script_frame_ceiling():
    from koma.steps.script import _frames

    assert _frames(4) == 107
    for duration in (0.1, 2, 4, 4.666, 14):
        assert _frames(duration) >= duration * 24
        assert (_frames(duration) - 5) % 17 == 0
    assert _frames(4, grid_fn=lambda seconds: 123) == 123
