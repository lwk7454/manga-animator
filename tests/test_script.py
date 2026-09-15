"""Durable feature tests; no live model services."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from koma.cli import app
from koma.clients import LLMError
from koma.models.domain import PanelState
from koma.steps.script import script_chapter
from tests.helpers import FakeClient, FakeOcr, _script_store

runner = CliRunner()


class TestScriptStage:
    def test_scripts_all_gated_panels(self, tmp_path: Path) -> None:
        store, slug = _script_store(tmp_path)
        from koma.steps.script import script_chapter

        summary = script_chapter(store, slug, 1, client=FakeClient(), ocr=FakeOcr())
        assert summary["scripted"] == 6 and summary["failed"] == 0
        script = json.loads(
            (store.chapter_dir(slug, 1) / "script.json").read_text(encoding="utf-8")
        )
        panels = script["panels"]
        assert len(panels) == 6
        first = next(iter(panels.values()))
        assert first["dialogue_jp"] == "おはよう"
        assert first["translations"]["en"] == "Good morning"
        assert first["translations"]["zh-HANS"] == "早上好"
        assert "hair" in first["motion_prompt"] or "turns" in first["motion_prompt"]
        assert (first["frames_17n5"] - 5) % 17 == 0
        assert all(pn.state is PanelState.SCRIPTED for pn in store.load_pages(slug)[0].panels)

    def test_vision_failure_marks_script_failed(self, tmp_path: Path) -> None:
        store, slug = _script_store(tmp_path)
        from koma.steps.script import script_chapter

        summary = script_chapter(store, slug, 1, client=FakeClient(fail_on="vision"), ocr=FakeOcr())
        assert summary["failed"] == 6 and summary["scripted"] == 0
        panels = [pn for pg in store.load_pages(slug) for pn in pg.panels]
        assert all(pn.state is PanelState.SCRIPT_FAILED for pn in panels)
        summary2 = script_chapter(store, slug, 1, client=FakeClient(), ocr=FakeOcr())
        assert summary2["scripted"] == 6

    def test_motion_failure_marks_script_failed(self, tmp_path: Path) -> None:
        store, slug = _script_store(tmp_path)
        from koma.steps.script import script_chapter

        summary = script_chapter(store, slug, 1, client=FakeClient(fail_on="motion"), ocr=FakeOcr())
        assert summary["failed"] == 6

    def test_idempotent_rerun_skips_scripted(self, tmp_path: Path) -> None:
        store, slug = _script_store(tmp_path)
        from koma.steps.script import script_chapter

        script_chapter(store, slug, 1, client=FakeClient(), ocr=FakeOcr())
        summary = script_chapter(store, slug, 1, client=FakeClient(), ocr=FakeOcr())
        assert summary["scripted"] == 0  # nothing left to do


class TestScriptCLI:
    def test_cli_dry_run(self, tmp_path: Path) -> None:
        store, slug = _script_store(tmp_path)
        r = runner.invoke(app, ["script", slug, "--dry-run", "--data-dir", str(store.data_dir)])
        assert r.exit_code == 0, r.output
        assert "6 eligible panel(s)" in r.output

    def test_cli_script_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store, slug = _script_store(tmp_path)
        import koma.steps.script as script_mod

        real_script = script_mod.script_chapter

        def offline_script(*args, **kwargs):
            return real_script(*args, **kwargs, client=FakeClient(), ocr=FakeOcr())

        monkeypatch.setattr(script_mod, "script_chapter", offline_script)
        r = runner.invoke(
            app,
            ["script", slug, "--data-dir", str(store.data_dir)],
        )
        assert r.exit_code == 0, r.output
        assert "scripted 6 panel(s), 0 failed" in r.output

    def test_cli_cards_empty(self, tmp_path: Path) -> None:
        store, slug = _script_store(tmp_path)
        r = runner.invoke(app, ["cards", slug, "--data-dir", str(store.data_dir)])
        assert r.exit_code == 0
        assert "registry: empty" in r.output


class TranslationClient(FakeClient):
    def __init__(self, failure=None):
        super().__init__()
        self.failure = failure
        self.translations = []

    def chat_json(self, model, messages, **kwargs):
        key = "en" if "English" in messages[-1]["content"] else "zh-HANS"
        self.translations.append(key)
        if key == "zh-HANS" and self.failure:
            if self.failure == "error":
                raise LLMError("temporarily unavailable")
            return self.failure
        return super().chat_json(model, messages, **kwargs)


@pytest.mark.parametrize(
    "failure",
    [
        "error",
        {"lines": []},
        {"lines": [{"zh-HANS": " "}]},
        {"lines": [{"zh-HANS": "ok"}, {}]},
        {"unexpected": True},
    ],
)
def test_missing_translation_blocks_and_retries_only_missing(tmp_path, failure):
    store, slug = _script_store(tmp_path)
    client = TranslationClient(failure)
    ocr = Mock(wraps=FakeOcr())
    first = script_chapter(store, slug, 1, client=client, ocr=ocr)
    assert first["failed"] == 6 and first["scripted"] == 0
    assert all(panel.state is PanelState.SCRIPT_FAILED for panel in store.all_panels(slug))
    assert store.load_chapters(slug)[0].state == "script_incomplete"
    entries = json.loads(Path(first["script"]).read_text())["panels"]
    assert all(entry["translations"]["en"] == "Good morning" for entry in entries.values())
    assert all(entry["complete"] is False for entry in entries.values())
    client.failure = None
    client.calls.clear()
    client.translations.clear()
    ocr.reset_mock()
    second = script_chapter(store, slug, 1, client=client, ocr=ocr)
    assert second["scripted"] == 6 and second["failed"] == 0
    assert client.translations == ["zh-HANS"] * 6
    assert all(prompt == "json" for _, prompt in client.calls)
    ocr.read_region.assert_not_called()
    assert store.load_chapters(slug)[0].state == "scripted"


def test_no_dialogue_requires_no_translations(tmp_path):
    store, slug = _script_store(tmp_path)
    client = TranslationClient("error")
    ocr = Mock()
    ocr.read_region.return_value = ""
    assert script_chapter(store, slug, 1, client=client, ocr=ocr)["failed"] == 0
    assert client.translations == []


def test_new_subtitle_language_reuses_other_steps(tmp_path):
    store, slug = _script_store(tmp_path)
    series = store.load_series(slug)
    series.meta.subtitle_languages = ["en"]
    store.save_series(series)
    client = TranslationClient()
    script_chapter(store, slug, 1, client=client, ocr=FakeOcr())
    series.meta.subtitle_languages.append("zh-HANS")
    store.save_series(series)
    client.calls.clear()
    client.translations.clear()
    ocr = Mock(wraps=FakeOcr())
    assert script_chapter(store, slug, 1, client=client, ocr=ocr)["scripted"] == 6
    assert client.translations == ["zh-HANS"] * 6
    assert all(prompt == "json" for _, prompt in client.calls)
    ocr.read_region.assert_not_called()


def test_stale_entry_cannot_revive_failed_script(tmp_path):
    store, slug = _script_store(tmp_path)
    pid = store.all_panels(slug)[0].panel_id
    path = store.chapter_dir(slug, 1) / "script.json"
    path.write_text(json.dumps({"panels": {pid: {"caption": "old", "complete": True}}}))
    result = script_chapter(
        store, slug, 1, client=FakeClient(fail_on="vision"), ocr=FakeOcr(), limit=1
    )
    assert result["failed"] == 1
    assert store.find_panel(slug, pid)[1].state is PanelState.SCRIPT_FAILED
    assert store.load_chapters(slug)[0].state == "script_incomplete"


def test_script_accepts_ordered_without_gate(tmp_path):
    store, slug = _script_store(tmp_path)
    pages = store.load_pages(slug)
    for panel in pages[0].panels:
        panel.state = PanelState.ORDERED
    store.save_pages(slug, pages)
    result = script_chapter(store, slug, 1, client=FakeClient(), ocr=FakeOcr(), limit=1)
    assert result["scripted"] == 1
    assert store.load_chapters(slug)[0].state == "script_incomplete"


def test_checkpoint_precedes_panel_advancement(tmp_path, monkeypatch):
    store, slug = _script_store(tmp_path)
    real_set = store.set_panel_state

    def interrupt(*args):
        raise RuntimeError("simulated interruption after checkpoint")

    monkeypatch.setattr(store, "set_panel_state", interrupt)
    with pytest.raises(RuntimeError, match="interruption"):
        script_chapter(store, slug, 1, client=FakeClient(), ocr=FakeOcr(), limit=1)
    monkeypatch.setattr(store, "set_panel_state", real_set)
    client = TranslationClient()
    ocr = Mock(wraps=FakeOcr())
    result = script_chapter(store, slug, 1, client=client, ocr=ocr, limit=1)
    assert result["scripted"] == 1
    assert client.calls == []
    ocr.read_region.assert_not_called()


def test_limited_source_revision_does_not_claim_old_entries_complete(tmp_path):
    store, slug = _script_store(tmp_path)
    script_chapter(store, slug, 1, client=FakeClient(), ocr=FakeOcr())
    page = store.load_pages(slug)[0]
    source = store.series_dir(slug) / "source" / page.source_path
    source.write_bytes(source.read_bytes() + b"new revision")
    result = script_chapter(store, slug, 1, client=FakeClient(), ocr=FakeOcr(), limit=1)
    assert result["scripted"] == 1
    assert store.load_chapters(slug)[0].state == "script_incomplete"


def test_restart_repairs_chapter_status_without_new_model_calls(tmp_path, monkeypatch):
    store, slug = _script_store(tmp_path)
    real_update = store.update_chapter
    monkeypatch.setattr(store, "update_chapter", Mock(side_effect=RuntimeError("interrupted")))
    with pytest.raises(RuntimeError, match="interrupted"):
        script_chapter(store, slug, 1, client=FakeClient(), ocr=FakeOcr())
    monkeypatch.setattr(store, "update_chapter", real_update)
    client = FakeClient()
    result = script_chapter(store, slug, 1, client=client, ocr=FakeOcr())
    assert result["scripted"] == 0 and client.calls == []
    assert store.load_chapters(slug)[0].state == "scripted"
