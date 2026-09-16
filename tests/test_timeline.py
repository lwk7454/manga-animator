"""P10 offline — timeline sample arithmetic and voice-binding gates (§17)."""

from __future__ import annotations

import pytest

from koma.adapters.tts import (
    MIX_SAMPLE_RATE,
    MeasuredAudio,
    TTSError,
    VoiceBinding,
    sequential_dialogue_timeline,
    validate_voice_binding,
)


def test_sequential_timeline_sample_exact():
    tl = sequential_dialogue_timeline(
        "p1", ["a", "b"], measured_durations_s=[1.0, 0.5], gap_ms=120
    )
    assert len(tl.events) == 2
    e0, e1 = tl.events
    assert e0.start_sample == 0 and e0.end_sample == MIX_SAMPLE_RATE
    gap = 120 * MIX_SAMPLE_RATE // 1000
    assert e1.start_sample == MIX_SAMPLE_RATE + gap
    assert e1.end_sample == e1.start_sample + MIX_SAMPLE_RATE // 2
    # content ends at the last audio, not after the trailing gap
    assert tl.content_end_sample == e1.end_sample


def test_no_speech_panel_is_finite_minimum():
    tl = sequential_dialogue_timeline("p1", [], no_speech=True)
    assert tl.content_end_sample == int(2.0 * MIX_SAMPLE_RATE)
    assert tl.events[0].kind == "ambience"
    # and it is finite (no BGM loop can extend it — BGM events are separate)


def test_placeholder_mode_has_no_positions():
    """Without measurements, no fake durations: recompute after synthesis."""
    tl = sequential_dialogue_timeline("p1", ["a"])
    assert tl.events == [] and tl.content_end_sample == 0


def test_nonpositive_measured_duration_rejected():
    with pytest.raises(TTSError):
        sequential_dialogue_timeline("p1", ["a"], measured_durations_s=[0.0])


def test_measured_audio_validation():
    with pytest.raises(TTSError):
        MeasuredAudio(48000, 2, 0).validate()  # empty waveform ≠ success
    with pytest.raises(TTSError):
        MeasuredAudio(0, 2, 100).validate()
    with pytest.raises(TTSError):
        MeasuredAudio(48000, 5, 100).validate()
    MeasuredAudio(48000, 2, 48000).validate()  # ok


def test_voice_binding_required_no_silent_replacement():
    with pytest.raises(Exception, match="no approved voice binding"):
        validate_voice_binding(None)
    vb = VoiceBinding("heroine:voice", 1, "gpt_sovits", "", "transcript")
    with pytest.raises(Exception, match="reference audio"):
        validate_voice_binding(vb)
    vb2 = VoiceBinding("heroine:voice", 1, "gpt_sovits", "art-1", "")
    with pytest.raises(Exception, match="transcript"):
        validate_voice_binding(vb2)
    ok = VoiceBinding("heroine:voice", 1, "gpt_sovits", "art-1", "こんにちは")
    assert validate_voice_binding(ok) is ok
