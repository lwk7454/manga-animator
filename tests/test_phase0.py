"""Unit tests — Phase 0: models, lifecycle, store, config, CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from koma.cli import app
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
from koma.models.lifecycle import (
    LEGAL_TRANSITIONS,
    MAINLINE,
    can_transition,
    next_state,
)
from koma.storage.series_store import SeriesStore

runner = CliRunner()


# ── fixtures ─────────────────────────────────────────────────────────────────


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


# ── lifecycle ────────────────────────────────────────────────────────────────


class TestLifecycle:
    def test_mainline_forward_is_legal(self) -> None:
        import itertools

        for a, b in itertools.pairwise(MAINLINE):
            assert can_transition(a, b), f"{a} → {b} must be legal"

    def test_skip_forward_is_legal(self) -> None:
        # color sources skip colorization: GATED → KEYFRAMED is legal
        assert can_transition(PanelState.GATED, PanelState.KEYFRAMED)

    def test_backward_is_illegal(self) -> None:
        assert not can_transition(PanelState.VALIDATED, PanelState.SCRIPTED)
        assert not can_transition(PanelState.UPSCALED, PanelState.DETECTED)

    def test_any_state_can_fail_or_need_regen(self) -> None:
        for state in MAINLINE:
            assert can_transition(state, PanelState.FAILED)
            assert can_transition(state, PanelState.NEEDS_REGEN)

    def test_failed_and_regen_requeue(self) -> None:
        assert can_transition(PanelState.FAILED, PanelState.QUEUED)
        assert can_transition(PanelState.NEEDS_REGEN, PanelState.QUEUED)

    def test_self_transition_is_idempotent(self) -> None:
        for state in PanelState:
            assert can_transition(state, state)

    def test_transition_table_covers_all_states(self) -> None:
        assert set(LEGAL_TRANSITIONS) == set(PanelState)

    def test_next_state_walks_mainline(self) -> None:
        assert next_state(PanelState.DETECTED) is PanelState.AWAITING_ORDER_REVIEW
        assert next_state(PanelState.ASSEMBLED) is None


# ── store ────────────────────────────────────────────────────────────────────


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


# ── config ───────────────────────────────────────────────────────────────────


class TestConfig:
    def test_defaults_from_committed_toml(self) -> None:
        s = Settings()
        assert s.video_profile_default == "minimax_h3_fl2va_turbo"
        assert s.text_llm == "qwen2.5-vl-7b"
        assert s.gpu_host == "192.168.50.103"
        assert s.gpu_port == 8188

    def test_paths_resolve_to_package_root(self) -> None:
        s = Settings()
        dd = s.resolved_data_dir()
        assert dd.is_absolute()
        assert dd.name == "data"

    def test_missing_config_uses_builtins(self, tmp_path: Path) -> None:
        s = Settings(tmp_path / "nonexistent.toml")
        assert s.video_profile_default == "minimax_h3_fl2va_turbo"

    def test_env_layering(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "c.toml").write_text("[text]\nllm = 'from-file'\n")
        s = Settings(tmp_path / "c.toml")
        assert s.text_llm == "from-file"
        assert s.env("KOMA_TEXT_API_KEY") is None
        monkeypatch.setenv("KOMA_TEXT_API_KEY", "secret-value")
        assert s.env("KOMA_TEXT_API_KEY") == "secret-value"  # env layer wins at call sites


# ── CLI ──────────────────────────────────────────────────────────────────────


class TestCLI:
    def test_new_and_status(self, tmp_path: Path) -> None:
        d = str(tmp_path / "d")
        r = runner.invoke(app, ["new", "clidemo", "--direction", "rtl", "--data-dir", d])
        assert r.exit_code == 0, r.output
        r2 = runner.invoke(app, ["status", "clidemo", "--data-dir", d])
        assert r2.exit_code == 0, r2.output
        assert "clidemo" in r2.output
        assert "chapters: none yet" in r2.output

    def test_new_rejects_bad_direction(self, tmp_path: Path) -> None:
        d = str(tmp_path / "d")
        r = runner.invoke(app, ["new", "x", "--direction", "bogus", "--data-dir", d])
        assert r.exit_code == 2

    def test_new_rejects_duplicate(self, tmp_path: Path) -> None:
        runner.invoke(app, ["new", "dup", "--data-dir", str(tmp_path / "d")])
        r = runner.invoke(app, ["new", "dup", "--data-dir", str(tmp_path / "d")])
        assert r.exit_code == 2

    def test_status_missing_series(self, tmp_path: Path) -> None:
        r = runner.invoke(app, ["status", "ghost", "--data-dir", str(tmp_path / "d")])
        assert r.exit_code == 2

    def test_status_shows_chapter_without_removed_scoring(self, tmp_path: Path) -> None:
        d = tmp_path / "d"
        runner.invoke(app, ["new", "s2", "--data-dir", str(d)])
        store = SeriesStore(d)
        store.ensure_chapter("s2", 1)
        store.set_chapter_gate("s2", 1, False, "seeded test page rejected")
        r = runner.invoke(app, ["status", "s2", "--data-dir", str(d)])
        assert r.exit_code == 0
        assert "ch01" in r.output
        assert "REJECTED" not in r.output
        assert "gate=" not in r.output

    def test_pause_resume(self, tmp_path: Path) -> None:
        d = tmp_path / "d"
        r1 = runner.invoke(app, ["pause", "--data-dir", str(d)])
        assert r1.exit_code == 0
        assert SeriesStore(d).is_paused()
        r2 = runner.invoke(app, ["resume", "--data-dir", str(d)])
        assert r2.exit_code == 0
        assert not SeriesStore(d).is_paused()

    def test_health_exits_zero(self, tmp_path: Path) -> None:
        r = runner.invoke(app, ["health", "--data-dir", str(tmp_path / "d")])
        assert r.exit_code == 0, r.output
        assert "ffmpeg" in r.output

    def test_version(self) -> None:
        r = runner.invoke(app, ["version"])
        assert r.exit_code == 0 and "koma" in r.output
