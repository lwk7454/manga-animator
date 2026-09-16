"""P16 integration — mux: audio attach, silence pad, honest mid-frame check.

Real ffmpeg/ffprobe. Guards the three user-visible regressions: black video,
silent video, duration drift.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from koma.mux import MuxError, extract_midframe, mux_audio, mux_silence

ffmpeg = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(ffmpeg is None, reason="ffmpeg not available")


def _make_clip(tmp_path: Path, seconds: float = 2.0) -> Path:
    """Real h264 clip: white box drifting on black, 24fps."""
    out = tmp_path / "clip.mp4"
    proc = subprocess.run(
        [ffmpeg, "-y",
         "-f", "lavfi", "-i",
         f"color=c=black:s=320x180:d={seconds}:r=24",
         "-f", "lavfi", "-i",
         f"color=c=white:s=60x60:d={seconds}:r=24",
         "-filter_complex",
         "[1]format=yuv420p[m];[0][m]overlay=x='20+t*50':y=60[v]",
         "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         str(out)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-300:]
    return out


def _make_tone_wav(tmp_path: Path, seconds: float, freq: int = 440) -> Path:
    out = tmp_path / "tone.wav"
    proc = subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:duration={seconds}",
         "-ar", "48000", "-ac", "2", str(out)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-300:]
    return out


def test_mux_audio_attaches_track_and_keeps_duration(tmp_path):
    video = _make_clip(tmp_path)
    audio = _make_tone_wav(tmp_path, 1.5)  # shorter than video → tail pad
    out = tmp_path / "muxed.mp4"
    res = mux_audio(video, audio, out)
    assert out.exists()
    assert res.has_audio
    assert abs(res.video_duration_s - 2.0) < 0.15
    # ffprobe: video + audio streams present, video codec not re-encoded
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)],
        capture_output=True, text=True, timeout=60)
    import json
    streams = json.loads(proc.stdout)["streams"]
    kinds = sorted(s["codec_type"] for s in streams)
    assert kinds == ["audio", "video"]
    v = next(s for s in streams if s["codec_type"] == "video")
    assert v["codec_name"] == "h264"


def test_mux_audio_trims_longer_audio(tmp_path):
    video = _make_clip(tmp_path, 2.0)
    audio = _make_tone_wav(tmp_path, 6.0)  # longer → trimmed by -shortest
    out = tmp_path / "muxed.mp4"
    res = mux_audio(video, audio, out)
    assert abs(res.output.stat().st_size) > 0
    assert abs(res.video_duration_s - 2.0) < 0.15


def test_mux_silence_gives_audio_stream(tmp_path):
    video = _make_clip(tmp_path)
    out = tmp_path / "silent.mp4"
    res = mux_silence(video, out)
    assert res.has_audio
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out)],
        capture_output=True, text=True, timeout=60)
    import json
    streams = json.loads(proc.stdout)["streams"]
    a = next(s for s in streams if s["codec_type"] == "audio")
    assert a["codec_name"] == "aac"


def test_extract_midframe_measures_content(tmp_path):
    video = _make_clip(tmp_path)
    png = tmp_path / "mid.png"
    mean = extract_midframe(video, png)
    assert mean > 10  # white box on black = real content


def test_extract_midframe_flags_black(tmp_path):
    # a genuinely black video must measure ~0 — the guard that failed Sep 16
    out = tmp_path / "black.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=2:r=24",
         "-c:v", "libx264", str(out)],
        capture_output=True, text=True, timeout=60)
    png = tmp_path / "mid.png"
    mean = extract_midframe(out, png)
    assert mean < 5


def test_mux_missing_inputs_raise(tmp_path):
    video = _make_clip(tmp_path)
    with pytest.raises(MuxError):
        mux_audio(video, tmp_path / "nope.wav", tmp_path / "o.mp4")
    with pytest.raises(MuxError):
        mux_audio(tmp_path / "nope.mp4", tmp_path / "x.wav", tmp_path / "o.mp4")
