"""P11 — real mixer tests: buses, BGM gain, ducking, silence padding (§18.1).

Real ffmpeg runs (marked): synthetic speech-envelope + tone fixtures measure
event placement, resting BGM gain, ducking effect and padding — with codec
tolerances, not exact bytes (§18.1).
"""

from __future__ import annotations

import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from koma.adapters.tts import MIX_SAMPLE_RATE
from koma.audio import (
    BGM_RESTING_GAIN,
    MixCue,
    MixSpec,
    build_filtergraph,
    mix_to_file,
    silence_pad_to_samples,
)

ffmpeg = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(ffmpeg is None, reason="ffmpeg not on PATH")


def _tone(
    path: Path, seconds: float, *, freq: float = 440.0, gain: float = 0.5
) -> Path:
    """Synthetic speech-envelope-ish tone at the mix rate (stereo PCM)."""
    t = np.linspace(0.0, seconds, int(seconds * MIX_SAMPLE_RATE), endpoint=False)
    env = np.minimum(1.0, 8.0 * t) * np.minimum(1.0, 8.0 * (seconds - t))
    wave_data = (np.sin(2 * np.pi * freq * t) * env * gain * 32767).astype(np.int16)
    stereo = np.column_stack([wave_data, wave_data])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(MIX_SAMPLE_RATE)
        w.writeframes(stereo.tobytes())
    return path


@pytest.fixture()
def tones(tmp_path: Path):
    return {
        "speech": _tone(tmp_path / "speech.wav", 1.0, freq=440),
        "long": _tone(tmp_path / "long.wav", 6.0, freq=220, gain=0.6),
        "sfx": _tone(tmp_path / "sfx.wav", 0.2, freq=1200, gain=0.8),
    }


def test_filtergraph_has_bgm_resting_gain_once(tones):
    spec = MixSpec(
        cues=[
            MixCue("dialogue", tones["speech"], 0),
            MixCue("bgm", tones["long"], 0),
        ],
        total_samples=6 * MIX_SAMPLE_RATE,
    )
    parts, n = build_filtergraph(spec)
    joined = ";".join(parts)
    # resting gain applied EXACTLY once; never re-amplified afterward
    assert joined.count(f"volume={BGM_RESTING_GAIN}") == 1
    assert "sidechaincompress" in joined
    assert n == 2


def test_mix_positions_sfx_at_requested_sample(tones, tmp_path: Path):
    spec = MixSpec(
        cues=[
            MixCue("dialogue", tones["speech"], 0),
            MixCue("sfx", tones["sfx"], 2 * MIX_SAMPLE_RATE),
        ],
        total_samples=4 * MIX_SAMPLE_RATE,
    )
    out = tmp_path / "mix.wav"
    info = mix_to_file(spec, out)
    assert info["samples"] == pytest.approx(4 * MIX_SAMPLE_RATE, rel=0.01)
    data = _read_stereo(out)
    # SFX band (1200 Hz) present around 2s, absent at 0.5s
    sfx_at_2s = _band_energy(data, 1.9, 2.1, 1200)
    sfx_at_05 = _band_energy(data, 0.4, 0.6, 1200)
    assert sfx_at_2s > 10 * max(sfx_at_05, 1e-9)


def test_bgm_resting_gain_measurable(tones, tmp_path: Path):
    """BGM alone: output level ~= 0.25 x source (not re-amplified)."""
    spec = MixSpec(cues=[MixCue("bgm", tones["long"], 0, gain=1.0)],
                   total_samples=6 * MIX_SAMPLE_RATE)
    out = tmp_path / "bgmonly.wav"
    mix_to_file(spec, out)
    data = _read_stereo(out)
    rms_out = _rms(data, 1.0, 3.0)
    src = _read_stereo(tones["long"])
    rms_src = _rms_arr(src[:, 0], 48000 * 1, 48000 * 3)
    assert rms_out == pytest.approx(BGM_RESTING_GAIN * rms_src, rel=0.2)


def test_ducking_reduces_bgm_under_speech(tones, tmp_path: Path):
    """Same BGM with and without dialogue: the overlapping band is quieter."""
    total = 4 * MIX_SAMPLE_RATE
    no_speech = tmp_path / "nospeech.wav"
    with_speech = tmp_path / "withspeech.wav"
    mix_to_file(MixSpec(cues=[MixCue("bgm", tones["long"], 0)],
                        total_samples=total), no_speech)
    mix_to_file(MixSpec(cues=[MixCue("bgm", tones["long"], 0),
                              MixCue("dialogue", tones["speech"], 0)],
                        total_samples=total), with_speech)
    d_quiet = _band_energy(_read_stereo(no_speech), 0.2, 0.8, 220)
    d_ducked = _band_energy(_read_stereo(with_speech), 0.2, 0.8, 220)
    assert d_ducked < d_quiet  # ducking measurably engaged (tolerant, §18.1)


def test_silence_pad_reaches_exact_samples(tones, tmp_path: Path):
    mix = tmp_path / "m.wav"
    spec = MixSpec(cues=[MixCue("dialogue", tones["speech"], 0)],
                   total_samples=3 * MIX_SAMPLE_RATE)
    mix_to_file(spec, mix)
    target = int(4.5 * MIX_SAMPLE_RATE)
    out = tmp_path / "padded.wav"
    info = silence_pad_to_samples(mix, target, out)
    assert info["samples"] == pytest.approx(target, rel=0.001)


def test_pad_rejects_content_longer_than_video(tones, tmp_path: Path):
    """Never truncate speech: content beyond target raises (§18.1)."""
    mix = tmp_path / "m.wav"
    spec = MixSpec(cues=[MixCue("dialogue", tones["long"], 0)],
                   total_samples=6 * MIX_SAMPLE_RATE)
    mix_to_file(spec, mix)
    with pytest.raises(Exception, match="truncating"):
        silence_pad_to_samples(mix, MIX_SAMPLE_RATE, tmp_path / "x.wav")


def test_zero_cue_spec_is_silence_of_exact_length(tmp_path: Path):
    out = tmp_path / "silence.wav"
    spec = MixSpec(cues=[], total_samples=2 * MIX_SAMPLE_RATE)
    info = mix_to_file(spec, out)
    assert info["samples"] == pytest.approx(2 * MIX_SAMPLE_RATE, rel=0.001)
    data = _read_stereo(out)
    assert np.abs(data).max() == 0


# ── helpers ──────────────────────────────────────────────────────────────────


def _read_stereo(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
    return data.reshape(-1, 2)


def _rms(data: np.ndarray, t0: float, t1: float) -> float:
    seg = data[int(t0 * MIX_SAMPLE_RATE):int(t1 * MIX_SAMPLE_RATE)]
    return float(np.sqrt(np.mean(seg ** 2)))


def _rms_arr(arr: np.ndarray, i0: int, i1: int) -> float:
    seg = arr[i0:i1]
    return float(np.sqrt(np.mean(seg ** 2)) + 1e-12)


def _band_energy(data: np.ndarray, t0: float, t1: float, freq: float) -> float:
    """Goertzel-style magnitude of `freq` in [t0,t1] (mono mixdown)."""
    seg = data[int(t0 * MIX_SAMPLE_RATE):int(t1 * MIX_SAMPLE_RATE)].mean(axis=1)
    return float(
        abs(np.sum(seg * np.exp(-2j * np.pi * freq * np.arange(seg.size) / MIX_SAMPLE_RATE)))
        / max(seg.size, 1)
    )
