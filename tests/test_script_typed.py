"""P06 — typed script substeps, translation completeness, checkpoints (§13).

§23 script rows: missing translation blocks; malformed result cannot mark
completion; partial valid artifacts reused; zero dialogue is explicit;
model output is schema-validated data (no prompt-chosen paths).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from koma.models.script import (
    CaptionSubstep,
    ChapterScript,
    OcrLine,
    OcrSubstep,
    PanelScript,
    TranslationEntry,
    TranslationSubstep,
    new_line_id,
)
from koma.steps.script_store import ScriptStore, load_typed_script


def _hash(s: str) -> str:
    import hashlib

    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _cap_payload(text: str, panel_id: str = "p1") -> dict:
    return {"panel_id": panel_id, "text": text, "model_identity": "qwen",
            "prompt_hash": _hash("p"), "input_fingerprint": _hash("i")}


def _panel(panel_id: str, lines: list[tuple[str, str]]) -> PanelScript:
    cap = CaptionSubstep(panel_id=panel_id, text="A scene", model_identity="qwen",
                         prompt_hash=_hash("p"), input_fingerprint=_hash("i"))
    ocr = OcrSubstep(
        panel_id=panel_id,
        lines=[OcrLine(line_id=new_line_id(1, panel_id, i + 1), source_text=txt)
               for i, (txt, _) in enumerate(lines)],
    )
    return PanelScript(panel_id=panel_id, caption=cap, ocr=ocr)


# ── translation completeness (D12) ──────────────────────────────────────────


def test_complete_translations_pass():
    ps = _panel("ch01_p001_01", [("おはよう", "")])
    ln = new_line_id(1, "ch01_p001_01", 1)
    ps.translations = TranslationSubstep(entries=[
        TranslationEntry(line_id=ln, language="en", text="Good morning"),
        TranslationEntry(line_id=ln, language="zh-HANS", text="早上好"),
    ])
    assert ps.translations_ready(["en", "zh-HANS"])


def test_missing_one_translation_blocks():
    ps = _panel("ch01_p001_01", [("おはよう", "")])
    ln = new_line_id(1, "ch01_p001_01", 1)
    ps.translations = TranslationSubstep(entries=[
        TranslationEntry(line_id=ln, language="en", text="Good morning"),
        # zh-HANS missing
    ])
    assert not ps.translations_ready(["en", "zh-HANS"])


def test_blank_translation_is_not_success():
    e = TranslationEntry(line_id="ln:x:1", language="en", text="   ")
    assert e.status == "retry_needed"


def test_empty_source_dialogue_needs_no_translation():
    ps = _panel("ch01_p001_01", [("", "")])  # no dialogue
    assert ps.translations_ready(["en", "zh-HANS"]) is True


def test_zero_dialogue_is_explicit_and_complete():
    ps = _panel("ch01_p001_01", [])
    assert ps.ocr_ready() and ps.translations_ready(["en", "zh-HANS"])


def test_malformed_entries_cannot_mark_completion():
    """Model output is data: wrong shape raises, never silently completes."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TranslationEntry(line_id="NOT-A-LINE", language="en", text="x")
    with pytest.raises(ValidationError):
        TranslationEntry(line_id="ln:x:1", language=123, text="x")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        # unknown fields are rejected — no prompt-chosen paths/commands
        TranslationEntry(line_id="ln:x:1", language="en", text="x", command="rm -rf /")


# ── chapter-level completeness ───────────────────────────────────────────────


def test_chapter_missing_translations_report():
    ch = ChapterScript(series_slug="s", chapter=1, panels={
        "p1": _panel("p1", [("こんにちは", "")]),
        "p2": _panel("p2", []),
    })
    missing = ch.missing_translations(["en", "zh-HANS"])
    assert "p1" in missing and "p2" not in missing


# ── substep store: checkpoint + reuse ────────────────────────────────────────


def test_substep_checkpoint_idempotent_fingerprint(tmp_path: Path):
    store = ScriptStore(tmp_path)
    payload = {"panel_id": "p1", "text": "hello", "model_identity": "qwen",
               "prompt_hash": _hash("p"), "input_fingerprint": _hash("i")}
    first = store.checkpoint("p1", "caption", payload)
    again = store.checkpoint("p1", "caption", payload)
    assert first["fingerprint"] == again["fingerprint"]
    doc = store.load("p1", "caption")
    assert doc["text"] == "hello" and doc["fingerprint"] == first["fingerprint"]


def test_changed_input_changes_fingerprint(tmp_path: Path):
    store = ScriptStore(tmp_path)
    f1 = store.checkpoint("p1", "caption", _cap_payload("v1"))
    f2 = store.checkpoint("p1", "caption", _cap_payload("v2"))
    assert f1["fingerprint"] != f2["fingerprint"]


def test_crash_only_repeats_uncheckpointed(tmp_path: Path):
    """Checkpoint-after-each-substep: prior substeps survive a crash."""
    store = ScriptStore(tmp_path)
    store.checkpoint("p1", "caption", _cap_payload("c"))
    store.checkpoint("p1", "ocr", {"panel_id": "p1", "lines": [
        {"line_id": "ln:p1:1", "source_text": "あ"}], "engine": "manga_ocr"})
    # "crash" before translations: loaded view has only the completed ones
    panel = store.load_panel("p1")
    assert set(panel) == {"caption", "ocr"}
    typed = load_typed_script(tmp_path, "s", 1)
    p = typed.panels["p1"]
    assert p.caption_ready() and p.ocr_ready()
    assert not p.translations_ready(["en"])


def test_partial_reuse_after_failure(tmp_path: Path):
    """Valid caption/OCR reused; only the failed translation re-runs (D12)."""
    store = ScriptStore(tmp_path)
    store.checkpoint("p1", "caption", _cap_payload("c"))
    store.checkpoint("p1", "translations", {"panel_id": "p1", "entries": [
        {"line_id": "ln:p1:1", "language": "en", "text": "ok", "status": "valid"},
    ]})
    before = store.panel_fingerprint("p1", "caption")
    # a later failed translation write does NOT touch the caption artifact
    store.checkpoint("p1", "translations", {"panel_id": "p1", "entries": []})
    assert store.panel_fingerprint("p1", "caption") == before
    typed = load_typed_script(tmp_path, "s", 1)
    assert typed.panels["p1"].caption_ready()


def test_speaker_mentions_are_unresolved_state(tmp_path: Path):
    from koma.models.script import SpeakerMention, SpeakerSubstep

    m = SpeakerMention(line_id="ln:p1:1", mention_id="girl_1", confidence=0.4)
    assert m.unresolved
    ps = _panel("p1", [("セリフ", "")])
    ps.speakers = SpeakerSubstep(panel_id="p1", mentions=[m])
    # unresolved speakers block production dialogue (§13.3)
    assert not ps.speakers_ready()


def test_explicit_no_speech_is_complete():
    from koma.models.script import SpeakerSubstep, SpeechSubstep

    ps = _panel("p1", [("セリフ", "")])
    ps.speech = SpeechSubstep(panel_id="p1", no_speech=True)
    ps.speakers = SpeakerSubstep(panel_id="p1")
    assert ps.speakers_ready()
