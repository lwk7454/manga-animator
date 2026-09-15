"""Durable feature tests; no live model services."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from koma.models.domain import PanelState
from tests.helpers import make_series


@pytest.mark.parametrize("answer,expected", [("ch01_p001_02", "ch01_p001_02"),
                                           ("ch01_p001_01", None),
                                           ("ch99_p001_01", None)])
def test_canonical_labels_follow_existing_images(tmp_path, monkeypatch, answer, expected):
    import koma.steps.cards as cards
    from koma.models.domain import CharacterCard

    store, cfg = make_series(tmp_path, PanelState.SCRIPTED)
    store.ensure_chapter("demo", 1)
    store.upsert_card("demo", CharacterCard(id="character", identity={"hair": "silver braided"}))
    chapter = store.chapter_dir("demo", 1)
    first, second = "ch01_p001_01", "ch01_p001_02"
    (chapter / "script.json").write_text(json.dumps({"panels": {
        first: {"caption": "silver braided"}, second: {"caption": "silver braided"}}}))
    (chapter / "panels" / first / "panel.png").unlink()
    second_image = chapter / "panels" / second / "panel.png"
    second_image.parent.mkdir()
    second_image.write_bytes(b"test")
    vision = Mock()
    vision.chat.return_value = answer
    monkeypatch.setattr(cards, "_vision_client", lambda config: vision)
    result = cards.pick_canonical_panels(store, "demo", cfg)["character"]
    assert result["canonical"] == expected
    assert result["candidates"] == [second]
    assert vision.chat.call_args.kwargs["images"] == [second_image]
    prompt = vision.chat.call_args.args[1][0]["content"]
    assert f"1. {second}" in prompt and first not in prompt
