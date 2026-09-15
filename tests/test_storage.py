"""Durable feature tests; no live model services."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from koma.config import Settings
from koma.models.domain import (
    BBox,
    CharacterCard,
    Job,
    Panel,
    PanelState,
    ReadingDirection,
    SeriesMeta,
    panel_key,
)
from koma.storage.series_store import SeriesStore


@pytest.fixture()
def store(tmp_path: Path) -> SeriesStore:
    s = SeriesStore(tmp_path / "data")
    s.create_series("demo", title="Demo", direction=ReadingDirection.RTL)
    return s


def _mk_panel(
    chapter: int = 1, page: int = 3, idx: int = 1, state: PanelState = PanelState.DETECTED
) -> Panel:
    return Panel(
        panel_id=panel_key(chapter, page, idx),
        bbox=BBox(x=10, y=20, w=300, h=200),
        state=state,
    )


def _store_with_panel(tmp_path: Path, state: PanelState) -> tuple[SeriesStore, str]:
    s = SeriesStore(tmp_path / "data")
    s.create_series("demo")
    s.ensure_chapter("demo", 1)
    from koma.models.domain import Page

    s.save_pages(
        "demo",
        [
            Page(
                chapter=1,
                page_number=3,
                source_path="ch01/p003.jpg",
                panels=[_mk_panel(state=state)],
            )
        ],
    )
    return s, _mk_panel().panel_id


class TestSeriesStore:
    def test_create_and_load_roundtrip(self, store: SeriesStore) -> None:
        series = store.load_series("demo")
        assert series.meta.slug == "demo"
        assert series.meta.direction is ReadingDirection.RTL
        assert series.meta.subtitle_languages == ["en", "zh-HANS"]

    def test_create_duplicate_rejected(self, store: SeriesStore) -> None:
        with pytest.raises(FileExistsError):
            store.create_series("demo")

    def test_slug_validation(self, tmp_path: Path) -> None:
        s = SeriesStore(tmp_path / "d")
        with pytest.raises(ValueError):
            s.create_series("Bad Slug!")

    def test_atomic_json_on_disk(self, store: SeriesStore) -> None:
        raw = json.loads((store.series_dir("demo") / "series.json").read_text())
        assert raw["meta"]["slug"] == "demo"

    def test_ensure_chapter_idempotent(self, store: SeriesStore) -> None:
        a = store.ensure_chapter("demo", 1)
        b = store.ensure_chapter("demo", 1)
        assert a.chapter_number == b.chapter_number == 1
        assert len(store.load_chapters("demo")) == 1

    def test_panel_crud_and_state_transition(self, tmp_path: Path) -> None:
        s, pid = _store_with_panel(tmp_path, PanelState.DETECTED)
        updated = s.set_panel_state("demo", pid, PanelState.AWAITING_ORDER_REVIEW)
        assert updated.state is PanelState.AWAITING_ORDER_REVIEW
        # backward transition raises (rework goes through NEEDS_REGEN, not backward)
        with pytest.raises(ValueError):
            s.set_panel_state("demo", pid, PanelState.DETECTED)

    def test_set_panel_state_idempotent(self, tmp_path: Path) -> None:
        s, pid = _store_with_panel(tmp_path, PanelState.ORDERED)
        again = s.set_panel_state("demo", pid, PanelState.ORDERED)
        assert again.state is PanelState.ORDERED

    def test_update_panel_immutable_copy(self, tmp_path: Path) -> None:
        s, pid = _store_with_panel(tmp_path, PanelState.DETECTED)
        out = s.update_panel("demo", pid, retries=2, error=None)
        assert out.retries == 2
        assert out.error is None

    def test_registry_upsert_and_find(self, store: SeriesStore) -> None:
        card = CharacterCard(id="akira", identity={"hair": "black"})
        store.upsert_card("demo", card)
        card.version = 2
        store.upsert_card("demo", card)
        reg = store.load_registry("demo")
        assert len(reg) == 1 and reg[0].version == 2
        assert store.find_card("demo", "akira").id == "akira"
        with pytest.raises(KeyError):
            store.find_card("demo", "nobody")

    def test_pause_flag_roundtrip(self, store: SeriesStore) -> None:
        assert not store.is_paused()
        store.set_paused(True)
        assert store.is_paused()
        store.set_paused(False)
        assert not store.is_paused()

    def test_job_key_and_upload_name_unique_shape(self, store: SeriesStore) -> None:
        key = store.job_key("demo", 1, "ch01_p003_01", "16:9", 1725871234)
        assert key == "demo_01_ch01_p003_01_16x9_1725871234"
        name = store.upload_name("ch01_p003_01", "9:16", "a1b2c3")
        assert name == "ch01_p003_01_9x16_kf_a1b2c3.png"

    def test_series_meta_defaults(self, tmp_path: Path) -> None:
        s = SeriesStore(tmp_path / "d2")
        s.create_series("m2")
        meta: SeriesMeta = s.load_series("m2").meta
        assert meta.direction is ReadingDirection.AUTO
        assert meta.language == "ja"
        assert meta.ratios == ["16:9", "9:16"]

    def test_job_model_defaults(self) -> None:
        job = Job(
            job_id="demo_01_ch01_p003_01_16x9_1", panel_id="ch01_p003_01", ratio="16:9", kind="i2v"
        )
        assert job.status.value == "pending"
        assert job.retries == 0


def test_default_data_directory():
    from koma.storage import default_data_dir

    assert default_data_dir() == Settings().resolved_data_dir()
