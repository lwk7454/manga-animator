"""Durable feature tests; no live model services."""

from __future__ import annotations

import pytest

from koma.models.domain import PanelState
from koma.scheduler.queue import load_queue
from tests.helpers import make_series


def test_render_chapter_100(tmp_path):
    from koma.steps.render import queue_render

    store, cfg = make_series(tmp_path, PanelState.KEYFRAMED)
    assert queue_render("demo", store, cfg, chapter=100)["queued"] == 1
    job, = load_queue(store.data_dir)["jobs"]
    assert job["chapter"] == 100 and "/100/" in job["output_mp4"]


@pytest.mark.parametrize("stage", ["keyframe", "render"])
def test_unknown_ratio_does_not_write_queue(tmp_path, stage):
    from koma.steps.keyframe import queue_keyframes
    from koma.steps.render import queue_render

    store, cfg = make_series(tmp_path, PanelState.COLORIZED)
    with pytest.raises(ValueError, match="unsupported ratio"):
        if stage == "keyframe":
            queue_keyframes("demo", store, cfg, ratios=["16:9", "1:1"])
        else:
            queue_render("demo", store, cfg, ratio="1:1")
    assert load_queue(store.data_dir) == {"jobs": []}
