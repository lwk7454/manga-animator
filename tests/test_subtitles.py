"""R7 — subtitle tests: SRT from measured timeline, language gating."""

from __future__ import annotations

from pathlib import Path

from koma.subtitles import build_srt, write_subtitles


def _tl():
    return [
        {"line_id": "ln:p1:0", "start": 0.0, "seconds": 2.25},
        {"line_id": "ln:p1:1", "start": 2.25, "seconds": 1.5},
    ]


def _lines():
    return {
        "ln:p1:0": {"jp": "きゃっ、こんなの初めて…。", "en": "Ah, this is my first time…"},
        "ln:p1:1": {"jp": "んっ…。", "en": "Mmm…"},
    }


def test_srt_times_come_from_measured_timeline():
    srt = build_srt(_tl(), _lines(), language="jp")
    assert "00:00:00,000 --> 00:00:02,250" in srt
    assert "00:00:02,250 --> 00:00:03,750" in srt
    assert "きゃっ、こんなの初めて…。" in srt
    assert "んっ…。" in srt


def test_srt_index_sequential_and_skips_blank():
    lines = {"ln:p1:0": {"jp": "one"}, "ln:p1:1": {"jp": "   "}}
    srt = build_srt(_tl(), lines, language="jp")
    assert srt.splitlines()[0] == "1"
    assert "2\n" not in srt  # only one cue
    assert "one" in srt


def test_language_gating():
    # en track exists when translations present; absent lines drop cues
    en = build_srt(_tl(), _lines(), language="en")
    assert "this is my first time" in en
    # jp-only lines: en track has no content → nothing rendered
    jp_only = {"ln:p1:0": {"jp": "一"}, "ln:p1:1": {"jp": "二"}}
    en2 = build_srt(_tl(), jp_only, language="en")
    assert en2.strip() == ""


def test_write_subtitles_creates_tracks(tmp_path: Path):
    paths = write_subtitles(_tl(), _lines(), tmp_path)
    names = {p.name for p in paths}
    assert names == {"subtitles_jp.srt", "subtitles_en.srt"}
    jp = (tmp_path / "subtitles_jp.srt").read_text(encoding="utf-8")
    assert "きゃっ" in jp


def test_write_skips_empty_language(tmp_path: Path):
    jp_only = {"ln:p1:0": {"jp": "一"}}
    tl = [{"line_id": "ln:p1:0", "start": 0.0, "seconds": 1.0}]
    paths = write_subtitles(tl, jp_only, tmp_path, languages=("jp",))
    assert [p.name for p in paths] == ["subtitles_jp.srt"]


def test_timestamp_rollover_format():
    # >1 hour, >1s rounding — formatting stays HH:MM:SS,mmm
    srt = build_srt(
        [{"line_id": "l1", "start": 3661.5, "seconds": 0.5}], {"l1": {"jp": "x"}}, language="jp"
    )
    assert "01:01:01,500 --> 01:01:02,000" in srt


def test_required_translation_blocks_all_track_writes(tmp_path):
    import pytest

    from koma.storage.errors import BlockedPrerequisite

    with pytest.raises(BlockedPrerequisite, match="required zh"):
        write_subtitles(_tl(), _lines(), tmp_path, languages=("jp", "en", "zh"))
    assert list(tmp_path.iterdir()) == []
