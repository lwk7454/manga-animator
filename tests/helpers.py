"""Shared synthetic inputs and local provider doubles."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from typer.testing import CliRunner

from koma.cli import app
from koma.config import Settings
from koma.models.domain import BBox, Page, Panel, PanelState, ReadingDirection
from koma.storage.series_store import SeriesStore

PAGE_W, PAGE_H = 1654, 2339  # A4 @ 200dpi


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
    for (x, y, w, h) in boxes:
        img[y:y + h, x:x + w] = rng.integers(40, 220, size=(1, 1, 3))
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 0), 6)  # border
    cv2.imwrite(str(path), img)


runner = CliRunner()


class FakeOcr:
    def read_region(self, page_path: Path, bbox: tuple[int, int, int, int]) -> str:
        return "おはよう"


class FakeClient:
    """Records calls; returns canned replies."""

    def __init__(self, fail_on: str | None = None):
        self.calls: list[tuple[str, str]] = []
        self.fail_on = fail_on

    def chat(self, model: str, messages: list, *, images=None, temperature=0.4, **kw):
        prompt = messages[-1]["content"]
        self.calls.append((model, str(prompt)[:50]))
        if self.fail_on == "vision" and images:
            from koma.clients import LLMError

            raise LLMError("vision down")
        if self.fail_on == "motion" and "image-to-video" in prompt:
            from koma.clients import LLMError

            raise LLMError("text down")
        if "image-to-video" in prompt:
            return "The girl turns her head, hair swaying in the breeze."
        return "A manga panel with two characters in a room."

    def chat_json(self, model: str, messages: list, **kw):
        self.calls.append((model, "json"))
        prompt = str(messages[-1]["content"])
        key = "en" if "English" in prompt else "zh-HANS"
        return {"lines": [{"jp": "おはよう", key: "Good morning" if key == "en" else "早上好"}]}


def _script_store(tmp_path: Path) -> tuple[SeriesStore, str]:
    """Series with ordered panels advanced through the legacy GATED compatibility state."""

    src = tmp_path / "src"
    src.mkdir()
    draw_page(src / "001.jpg", PANELS_GRID)
    d = tmp_path / "data"
    runner.invoke(app, ["new", "s", "--direction", "rtl", "--data-dir", str(d)])
    runner.invoke(app, ["ingest", "s", str(src), "--data-dir", str(d)])
    runner.invoke(app, ["certify", "s", "--page", "1", "--data-dir", str(d)])
    store = SeriesStore(d)
    # advance ORDERED → GATED (exercising the legacy compatibility state)
    for pn in store.load_pages("s")[0].panels:
        store.set_panel_state("s", pn.panel_id, PanelState.GATED)
    return store, "s"


CONFIG = Path(__file__).resolve().parents[1] / "config"


def make_series(tmp_path, state):
    store = SeriesStore(tmp_path / "data")
    store.create_series("demo", ratios=["9:16"])
    pages = []
    for ch in (1, 100):
        pid = f"ch{ch:02d}_p001_01"
        pages.append(Page(chapter=ch, page_number=1, source_path="source.png", width=10,
                          height=10, direction_resolved=ReadingDirection.RTL,
                          panels=[Panel(panel_id=pid, order=1, state=state,
                                        bbox=BBox(x=0, y=0, w=10, h=10))]))
        pdir = store.chapter_dir("demo", ch) / "panels" / pid
        pdir.mkdir(parents=True)
        for name in ("panel.png", "keyframe_1024x576.png"):
            (pdir / name).write_bytes(b"test")
        (store.chapter_dir("demo", ch) / "script.json").write_text(
            json.dumps({"panels": {pid: {"caption": "test"}}}))
    store.save_pages("demo", pages)
    cfg = Settings()
    cfg._data = {"paths": {"data": str(store.data_dir), "config": str(CONFIG)}}
    return store, cfg


def seed_binding_subjects(db, series_id, chapter_id):
    """Persist real subjects for binding store/API fixtures."""
    with db.transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO chapters "
            "(id,series_id,number,created_at,updated_at) VALUES "
            "(?,?,1,datetime('now'),datetime('now'))",
            (chapter_id, series_id),
        )
        conn.execute(
            "INSERT INTO pages "
            "(id,chapter_id,series_id,birth_number,sequence_position,source_path,"
            "created_at,updated_at) "
            "VALUES "
            "('binding-page',?,?,1,1,'source.png',datetime('now'),datetime('now'))",
            (chapter_id, series_id),
        )
        for i, pid in enumerate(["ch01_p001_01", "ch01_p001_02", "ch01_p009_05"], 1):
            conn.execute(
                "INSERT INTO panels "
                "(id,page_id,series_id,chapter_id,birth_ordinal,reading_order,bbox_x,"
                "bbox_y,bbox_w,bbox_h,created_at,updated_at) "
                "VALUES "
                "(?,'binding-page',?,?,?,?,0,0,10,10,datetime('now'),datetime('now'))",
                (pid, series_id, chapter_id, i, i),
            )
        for shot in ["shot_a", "shot_x"]:
            conn.execute(
                "INSERT INTO shot_plan_revisions "
                "(id,series_id,chapter_id,shot_id,revision,beat_coverage,source_panel"
                "_ids,created_at,updated_at) "
                "VALUES (?,?,?,?,1,'[]','[]',datetime('now'),datetime('now'))",
                (shot, series_id, chapter_id, shot),
            )
