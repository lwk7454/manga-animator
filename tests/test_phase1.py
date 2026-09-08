"""Phase 1 tests — ingest (detection + order) and review sheets.

Uses synthetic manga pages drawn with PIL: white page, black-bordered
rectangular panels with distinct fills. Ground-truth bboxes are known, so
detection + reading order are asserted exactly.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from typer.testing import CliRunner

from koma.cli import app
from koma.models.domain import BBox, PanelState, ReadingDirection
from koma.steps.ingest import (
    detect_panels_bboxes,
    propose_reading_order,
)
from koma.storage.series_store import SeriesStore

runner = CliRunner()

PAGE_W, PAGE_H = 1654, 2339  # A4 @ 200dpi

# (x, y, w, h) grid — 6 panels, manga-style: 3 rows x varying columns.
# Row 1: two panels (right half wide), row 2: three panels, row 3: one wide + one
PANELS_GRID = [
    (854, 84, 716, 656),    # top-right (read first in RTL)
    (84, 84, 736, 656),     # top-left
    (1224, 774, 346, 626),  # mid-right
    (654, 774, 536, 626),   # mid-center
    (84, 774, 536, 626),    # mid-left
    (84, 1434, 1486, 800),  # bottom full-width
]


def draw_page(path: Path, boxes: list[tuple[int, int, int, int]]) -> None:
    img = np.full((PAGE_H, PAGE_W, 3), 255, dtype=np.uint8)
    rng = np.random.default_rng(7)
    for (x, y, w, h) in PANELS_GRID:
        img[y:y + h, x:x + w] = rng.integers(40, 220, size=(1, 1, 3))
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 0), 6)  # border
    cv2.imwrite(str(path), img)


@pytest.fixture()
def fixture_page(tmp_path: Path) -> Path:
    p = tmp_path / "page.jpg"
    draw_page(p, PANELS_GRID)
    return p


class TestDetection:
    def test_detects_all_six_panels(self, fixture_page: Path) -> None:
        det = detect_panels_bboxes(fixture_page)
        assert len(det.boxes) == 6

    def test_boxes_match_ground_truth(self, fixture_page: Path) -> None:
        det = detect_panels_bboxes(fixture_page)
        got = sorted((b.x, b.y, b.w, b.h) for b in det.boxes)
        want = sorted(PANELS_GRID)
        for g, w in zip(got, want, strict=False):
            assert abs(g[0] - w[0]) <= 12 and abs(g[1] - w[1]) <= 12  # ±12px tolerance
            assert abs(g[2] - w[2]) <= 12 and abs(g[3] - w[3]) <= 12

    def test_low_confidence_flag_on_splash(self, tmp_path: Path) -> None:
        # single huge panel = splash → flagged
        img = np.full((PAGE_H, PAGE_W, 3), 255, dtype=np.uint8)
        cv2.rectangle(img, (40, 40), (PAGE_W - 40, PAGE_H - 40), (0, 0, 0), 6)
        img[46:PAGE_H - 46, 46:PAGE_W - 46] = 60
        p = tmp_path / "splash.jpg"
        cv2.imwrite(str(p), img)
        det = detect_panels_bboxes(p)
        assert len(det.boxes) == 1
        assert 0 in det.low_confidence


def _mk_boxes() -> list[BBox]:
    return [BBox(x=x, y=y, w=w, h=h) for x, y, w, h in PANELS_GRID]


class TestReadingOrder:
    def test_rtl_order(self) -> None:
        order, low = propose_reading_order(_mk_boxes(), ReadingDirection.RTL, PAGE_H)
        # row 1: right panel (idx 0) before left (idx 1); bottom full-width last
        assert order[0] == 0 and order[1] == 1
        assert order[-1] == 5
        # rows in top→bottom sequence
        ys = [PANELS_GRID[i][1] for i in order]
        assert ys == sorted(ys)
        assert not low

    def test_ltr_order(self) -> None:
        order, _ = propose_reading_order(_mk_boxes(), ReadingDirection.LTR, PAGE_H)
        assert order[0] == 1 and order[1] == 0  # left before right in row 1

    def test_staggered_row_flagged_low(self) -> None:
        # two panels in same bucket but very different vertical centers
        boxes = [
            BBox(x=900, y=100, w=600, h=200),
            BBox(x=100, y=200, w=600, h=200),
        ]
        _order, low = propose_reading_order(boxes, ReadingDirection.RTL, PAGE_H)
        assert low == {0, 1}


class TestIngestCLI:
    def test_ingest_jpeg_dir(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        draw_page(src / "001.jpg", PANELS_GRID)
        draw_page(src / "002.jpg", PANELS_GRID)
        d = tmp_path / "data"
        r = runner.invoke(app, ["new", "fx", "--data-dir", str(d)])
        assert r.exit_code == 0, r.output
        r = runner.invoke(app, ["ingest", "fx", str(src), "--data-dir", str(d)])
        assert r.exit_code == 0, r.output
        assert "2 page(s)" in r.output and "12 panel(s)" in r.output
        store = SeriesStore(d)
        pages = store.load_pages("fx")
        assert len(pages) == 2
        assert pages[0].panels[0].state is PanelState.AWAITING_ORDER_REVIEW
        # RTL: first panel on page is the top-right one
        assert pages[0].panels[0].bbox.x > PAGE_W / 2
        # idempotent re-run
        r = runner.invoke(app, ["ingest", "fx", str(src), "--data-dir", str(d)])
        assert r.exit_code == 0 and "2 page(s)" in r.output

    def test_ingest_rtl_order_within_page(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        draw_page(src / "001.jpg", PANELS_GRID)
        d = tmp_path / "data"
        runner.invoke(app, ["new", "rtl1", "--direction", "rtl", "--data-dir", str(d)])
        r = runner.invoke(app, ["ingest", "rtl1", str(src), "--data-dir", str(d)])
        assert r.exit_code == 0
        store = SeriesStore(d)
        page = store.load_pages("rtl1")[0]
        # order 1 = top-right, order 2 = top-left, last = full-width bottom
        assert page.panels[0].bbox.x > PAGE_W / 2 and page.panels[0].bbox.y < 200
        assert page.panels[1].bbox.x < PAGE_W / 2
        assert page.panels[-1].bbox.w > PAGE_W * 0.8

    def test_certify_headless_with_swap(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        draw_page(src / "001.jpg", PANELS_GRID)
        d = tmp_path / "data"
        runner.invoke(app, ["new", "c1", "--direction", "rtl", "--data-dir", str(d)])
        runner.invoke(app, ["ingest", "c1", str(src), "--data-dir", str(d)])
        r = runner.invoke(
            app, ["certify", "c1", "--page", "1", "--swap", "0,1", "--data-dir", str(d)]
        )
        assert r.exit_code == 0, r.output
        store = SeriesStore(d)
        page = store.load_pages("c1")[0]
        assert all(pn.state is PanelState.ORDERED for pn in page.panels)
        # after swap, first panel is the top-LEFT one
        assert page.panels[0].bbox.x < PAGE_W / 2

    def test_status_shows_awaiting_counts(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        draw_page(src / "001.jpg", PANELS_GRID)
        d = tmp_path / "data"
        runner.invoke(app, ["new", "s9", "--data-dir", str(d)])
        runner.invoke(app, ["ingest", "s9", str(src), "--data-dir", str(d)])
        r = runner.invoke(app, ["status", "s9", "--data-dir", str(d)])
        assert r.exit_code == 0
        assert "awaiting_order_review=6" in r.output

    def test_review_sheet_generated(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        draw_page(src / "001.jpg", PANELS_GRID)
        d = tmp_path / "data"
        runner.invoke(app, ["new", "rv1", "--data-dir", str(d)])
        runner.invoke(app, ["ingest", "rv1", str(src), "--data-dir", str(d)])
        from koma.review.sheets import generate_review_page

        store = SeriesStore(d)
        out = store.series_dir("rv1") / "review" / "order_review.html"
        n = generate_review_page(store, "rv1", out)
        assert n == 1
        html = out.read_text()
        assert "data-page=" in html and "certify" in html

    def test_ingest_missing_series(self, tmp_path: Path) -> None:
        d = str(tmp_path / "d")
        r = runner.invoke(app, ["ingest", "ghost", str(tmp_path), "--data-dir", d])
        assert r.exit_code == 2
