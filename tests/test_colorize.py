"""Durable feature tests; no live model services."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from koma.models.domain import BBox, JobStatus, Page, Panel, PanelState, ReadingDirection
from koma.scheduler.queue import load_queue
from tests.helpers import make_series


@pytest.fixture
def mini_series(tmp_path, monkeypatch):
    """A tiny series with one SCRIPTED panel + card registry, config pointed at tmp."""
    from koma.models.domain import PanelState
    from koma.storage.series_store import SeriesStore

    data = tmp_path / "data"
    store = SeriesStore(data)
    store.create_series("testseries", direction=ReadingDirection.RTL, language="ja")
    # inject one page with one panel directly at SCRIPTED

    page = Page(
        chapter=1,
        page_number=1,
        source_path="ch01/p001.jpg",
        width=1000,
        height=1400,
        direction_resolved=ReadingDirection.RTL,
        panels=[
            Panel(
                panel_id="ch01_p001_01",
                bbox=BBox(x=0, y=0, w=500, h=700),
                state=PanelState.SCRIPTED,
            ),
        ],
    )
    store.save_pages("testseries", [page])
    # script.json
    ch_dir = store.chapter_dir("testseries", 1)
    (ch_dir / "panels" / "ch01_p001_01").mkdir(parents=True)
    (ch_dir / "panels" / "ch01_p001_01" / "panel.png").write_bytes(b"PNG")
    (ch_dir / "script.json").write_text(
        json.dumps(
            {
                "panels": {
                    "ch01_p001_01": {
                        "panel_id": "ch01_p001_01",
                        "caption": "a girl in a garden",
                        "characters": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return store


class TestColorizeStep:
    def test_queue_and_reconcile_happy_path(self, mini_series, tmp_path, monkeypatch):
        from koma.steps import colorize as cz

        fake_settings = type(
            "S",
            (),
            {
                "resolved_config_dir": lambda self: Path(__file__).parent.parent / "config",
            },
        )()

        result = cz.queue_colorize("testseries", mini_series, fake_settings)
        assert result["queued"] == 1
        # panel STAYS SCRIPTED at queue time (decision #028: no queue-time
        # state flips — the monitor advances states only on download)
        _pg, panel = mini_series.find_panel("testseries", "ch01_p001_01")
        assert panel.state is PanelState.SCRIPTED

        # idempotent: job dedupe by id — second run adds nothing new
        again = cz.queue_colorize("testseries", mini_series, fake_settings)
        assert again["queued"] == 0 and again["skipped"] == 1
        _pg, panel = mini_series.find_panel("testseries", "ch01_p001_01")
        assert panel.state is PanelState.SCRIPTED  # unchanged

        # reconcile with a fake worker: job is pending w/o prompt_id → submitted
        class FakeWorker:
            def __init__(self):
                self.downloads = []

            def poll(self, pid):
                return JobStatus.GENERATED

            def download_output(self, pid, out):
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b"COLOR")
                self.downloads.append(pid)

        fw = FakeWorker()
        res = cz.reconcile_colorize("testseries", mini_series, fake_settings, fw)
        # no prompt_id yet → nothing to poll; monitor owns submission
        assert res == {"downloaded": 0, "failed": 0}

    def test_color_source_series_skips_gpu(self, tmp_path):
        """A color-source series never queues GPU jobs: color.png = panel.png
        and panels jump straight to COLORIZED (native-color skip-forward)."""
        from koma.models.domain import PanelState, ReadingDirection
        from koma.steps import colorize as cz
        from koma.storage.series_store import SeriesStore

        data = tmp_path / "data"
        store = SeriesStore(data)
        store.create_series("colorseries", direction=ReadingDirection.RTL, language="ja")
        # flip the series to color source
        series = store.load_series("colorseries")
        series.meta.is_color_source = True
        store.save_series(series)

        page = Page(
            chapter=1,
            page_number=1,
            source_path="ch01/p001.jpg",
            width=1000,
            height=1400,
            direction_resolved=ReadingDirection.RTL,
            panels=[
                Panel(
                    panel_id="ch01_p001_01",
                    bbox=BBox(x=0, y=0, w=500, h=700),
                    state=PanelState.SCRIPTED,
                ),
            ],
        )
        store.save_pages("colorseries", [page])
        ch_dir = store.chapter_dir("colorseries", 1)
        (ch_dir / "panels" / "ch01_p001_01").mkdir(parents=True)
        (ch_dir / "panels" / "ch01_p001_01" / "panel.png").write_bytes(b"PANEL")
        (ch_dir / "script.json").write_text(
            json.dumps(
                {
                    "panels": {
                        "ch01_p001_01": {
                            "panel_id": "ch01_p001_01",
                            "caption": "x",
                            "characters": [],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        fake_settings = type(
            "S", (), {"resolved_config_dir": lambda self: Path(__file__).parent.parent / "config"}
        )()
        result = cz.queue_colorize("colorseries", store, fake_settings)
        assert result["queued"] == 0
        assert result["skipped"] == 1
        color = ch_dir / "panels" / "ch01_p001_01" / "color.png"
        assert color.read_bytes() == b"PANEL"  # copied, not generated
        _pg, panel = store.find_panel("colorseries", "ch01_p001_01")
        assert panel.state is PanelState.COLORIZED


@pytest.mark.parametrize("color_source", [False, True])
def test_colorize_chapter_100_and_color_copy_filter(tmp_path, color_source):
    from koma.steps.colorize import queue_colorize

    store, cfg = make_series(tmp_path, PanelState.SCRIPTED)
    series = store.load_series("demo")
    series.meta.is_color_source = color_source
    store.save_series(series)
    result = queue_colorize("demo", store, cfg, chapter=100)
    assert store.find_panel("demo", "ch01_p001_01")[1].state == PanelState.SCRIPTED
    if color_source:
        assert result["skipped_panels"] == ["ch100_p001_01"]
    else:
        job, = load_queue(store.data_dir)["jobs"]
        assert job["chapter"] == 100 and "/100/" in job["panel_png"]
