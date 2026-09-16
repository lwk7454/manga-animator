"""Final audio integration: mux the mixed dialogue bus onto a video clip.

Doctrine (§16.4 + §18): the H3-generated soundtrack is discarded (noise);
the REAL audio lane is TTS dialogue on the measured timeline, mixed by
``koma.audio`` (BGM 0.25 + ducking), then muxed onto the video with
``-c:v copy`` (video is never re-encoded) and AAC output. Output duration
equals the video; audio is trimmed/padded, never speed-adjusted (B15).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class MuxError(RuntimeError):
    pass


def _ffprobe_duration_s(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise MuxError(f"ffprobe failed on {path}: {proc.stderr[-200:]}")
    return float(proc.stdout.strip())


@dataclass
class MuxResult:
    output: Path
    video_duration_s: float
    audio_duration_s: float
    has_audio: bool


def mux_audio(
    video: Path,
    audio: Path,
    output: Path,
    *,
    verify_content: bool = True,
) -> MuxResult:
    """Mux ``audio`` onto ``video`` → ``output`` (video stream copied).

    Audio is the pad/trim side: if audio is longer than video it is trimmed
    to video duration; if shorter, silence pads the tail. Never resamples
    video timing.
    """
    if not video.exists():
        raise MuxError(f"video missing: {video}")
    if not audio.exists():
        raise MuxError(f"audio missing: {audio}")

    vdur = _ffprobe_duration_s(video)
    adur = _ffprobe_duration_s(audio)

    # Audio is the pad/trim side: pad to video duration first, then mux.
    # -shortest would TRIM THE VIDEO when audio is shorter — that violates
    # the audio-first doctrine (video duration is never altered).
    padded = audio.parent / (audio.stem + "_pad48k.wav")
    cmd_pad = [
        "ffmpeg", "-y", "-i", str(audio),
        "-af", f"apad=whole_dur={vdur:.3f}",
        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
        str(padded),
    ]
    proc_pad = subprocess.run(cmd_pad, capture_output=True, text=True, timeout=120)
    if proc_pad.returncode != 0:
        raise MuxError(f"audio pad failed: {proc_pad.stderr[-300:]}")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video), "-i", str(padded),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise MuxError(f"mux failed: {proc.stderr[-400:]}")

    odur = _ffprobe_duration_s(output)
    if abs(odur - vdur) > 0.15:
        raise MuxError(
            f"muxed duration {odur:.2f}s drifts from video {vdur:.2f}s — audio "
            "pad/trim logic violated (never speed-adjust video)"
        )
    result = MuxResult(output=output, video_duration_s=vdur,
                       audio_duration_s=adur, has_audio=True)
    return result


def mux_silence(
    video: Path,
    output: Path,
) -> MuxResult:
    """Attach a silent stereo track (48kHz AAC) matching video duration.

    Used when a clip legitimately has no dialogue yet must not be silent-
    flagged by downstream validators that expect an audio stream.
    """
    if not video.exists():
        raise MuxError(f"video missing: {video}")
    vdur = _ffprobe_duration_s(video)
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-i", str(video),
        "-map", "1:v:0", "-map", "0:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-t", f"{vdur:.3f}",
        "-shortest",
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise MuxError(f"silence mux failed: {proc.stderr[-400:]}")
    odur = _ffprobe_duration_s(output)
    if abs(odur - vdur) > 0.15:
        raise MuxError(f"silence-mux duration {odur:.2f}s != video {vdur:.2f}s")
    return MuxResult(output=output, video_duration_s=vdur,
                     audio_duration_s=vdur, has_audio=True)


def extract_midframe(video: Path, out_png: Path, at_s: float = 1.5) -> int:
    """Decode one mid-frame and return its gray mean — the honest content check.

    mean > 10 ⇒ real content; ~0 ⇒ black video (must never pass promotion).
    """

    proc = subprocess.run(
        ["ffmpeg", "-ss", f"{at_s:.2f}", "-i", str(video), "-frames:v", "1",
         "-y", str(out_png)],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise MuxError(f"frame extract failed: {proc.stderr[-200:]}")
    import cv2

    img = cv2.imread(str(out_png), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise MuxError("frame decode returned no image")
    return round(float(img.mean()))


__all__ = ["MuxError", "MuxResult", "extract_midframe", "mux_audio", "mux_silence"]
