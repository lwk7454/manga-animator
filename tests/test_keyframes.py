"""Durable feature tests; no live model services."""

from __future__ import annotations

from unittest.mock import Mock

from typer.testing import CliRunner

from koma.cli import app
from koma.models.domain import PanelState
from koma.scheduler.queue import load_queue
from tests.helpers import make_series


def test_keyframes_series_ratios_and_chapter_100(tmp_path):
    from koma.steps.keyframe import queue_keyframes

    store, cfg = make_series(tmp_path, PanelState.COLORIZED)
    assert queue_keyframes("demo", store, cfg, chapter=100)["queued"] == 1
    job, = load_queue(store.data_dir)["jobs"]
    assert job["chapter"] == 100 and job["ratio"] == "9:16"
    assert "/100/" in job["output_png"]


def test_keyframes_cli_forwards_chapter(tmp_path, monkeypatch):
    import koma.steps.keyframe as keyframe

    store, _ = make_series(tmp_path, PanelState.COLORIZED)
    planner = Mock(return_value=dict(queued=0, skipped=0, ratios=["9:16"], profile="test"))
    monkeypatch.setattr(keyframe, "queue_keyframes", planner)
    result = CliRunner().invoke(app, ["keyframes", "demo", "--chapter", "100",
                                     "--data-dir", str(store.data_dir)])
    assert result.exit_code == 0, result.output
    assert planner.call_args.kwargs["chapter"] == 100
