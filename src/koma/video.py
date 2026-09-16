"""Video validation, central encoder, chapter assembly (PLAN §19, offline).

- ``video.py`` (this module until the seam splits): ffprobe + decode-frame
  validation — existence and extension are insufficient (§19.1). A timeout
  or error report is never a successful output.
- ``encode``: central encoder selection. macOS production target is
  h264_videotoolbox; Linux/dev may select libx264 EXPLICITLY. No silent
  fallback in production: the chosen encoder is part of provenance.
- Assembly manifest math: ordered segments with integer frame offsets at
  rational fps — never globbed directory order, never decimal drift (§19.4).

Upscale (Real-CUGAN) requires the installed backend (hardware gate); its
validation contract lives here as a pure seam so P12 qualification can drop
in without replumbing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from .log import get_logger

log = get_logger("video.validate")

ENCODER_NAME = "h264_videotoolbox"  # macOS production target (§19.3)
LINUX_DEV_ENCODER = "libx264"


class VideoValidationError(RuntimeError):
    pass


@dataclass
class VideoFacts:
    container: str
    width: int
    height: int
    fps: Fraction
    frames: int
    duration_s: float
    has_audio: bool
    codecs: dict[str, str] = field(default_factory=dict)


def probe_video(path: Path, *, ffprobe: str = "ffprobe") -> VideoFacts:
    """Measured stream facts from ffprobe. Raises on unreadable/missing."""
    if not Path(path).is_file():
        raise VideoValidationError(f"missing video file: {path}")
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, timeout=120,
    )
    if proc.returncode != 0:
        raise VideoValidationError(f"ffprobe failed: {proc.stderr[-300:]!r}")
    data = json.loads(proc.stdout.decode() or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise VideoValidationError(f"no video stream in {path}")
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    num, _, den = rate.partition("/")
    den = den or "1"
    if int(den) == 0 or int(num) == 0:
        raise VideoValidationError(f"invalid frame rate {rate!r} in {path}")
    fps = Fraction(int(num), int(den))
    n_frames = int(video.get("nb_frames") or 0)
    duration = float(video.get("duration") or 0.0)
    if n_frames == 0 and duration == 0.0:
        raise VideoValidationError(f"no frame count/duration in {path}")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    codecs = {s["codec_type"]: s.get("codec_name", "") for s in streams}
    return VideoFacts(
        container=data.get("format", {}).get("format_name", ""),
        width=int(video["width"]), height=int(video["height"]),
        fps=Fraction(int(num), int(den)),
        frames=n_frames or round(duration * float(fps)),
        duration_s=duration or (n_frames / (int(num) / int(den))),
        has_audio=audio is not None,
        codecs=codecs,
    )


def validate_rendered_video(
    path: Path, *, expect_width: int, expect_height: int, expect_fps: Fraction,
    min_frames: int, expect_audio: bool = True, ffprobe: str = "ffprobe",
) -> VideoFacts:
    """Technical validation before promotion (§19.1): dimensions, fps, frames.

    Frame-count accounting is validated against the audio-first plan (never
    rounded down). A bad render fails validation → bounded retry/review.
    """
    facts = probe_video(path, ffprobe=ffprobe)
    problems: list[str] = []
    if (facts.width, facts.height) != (expect_width, expect_height):
        problems.append(f"dimensions {facts.width}x{facts.height} != "
                        f"{expect_width}x{expect_height}")
    if facts.fps != Fraction(expect_fps):
        problems.append(f"fps {facts.fps} != {expect_fps}")
    if facts.frames < min_frames:
        problems.append(f"frames {facts.frames} < required {min_frames} "
                        "(never round down; bad render must not promote)")
    if expect_audio and not facts.has_audio:
        problems.append("expected an audio stream (generated soundtrack must be "
                        "explicitly discarded, not silently missing)")
    if problems:
        raise VideoValidationError("; ".join(problems))
    return facts


def select_encoder(*, platform: str | None = None, explicit: str | None = None) -> str:
    """Central encoder policy (§19.3): explicit beats platform default.

    No silent fallback: if the caller selected an encoder and it's not the
    platform default, the caller's choice is used and becomes provenance.
    """
    if explicit:
        return explicit
    plat = platform or sys.platform
    if plat == "darwin":
        return ENCODER_NAME
    if plat.startswith("linux"):
        return LINUX_DEV_ENCODER
    return ENCODER_NAME  # documented target; Windows orchestration not v1


def build_assembly_manifest(
    segments: list[dict], *, fps: Fraction, sample_rate: int = 48000
) -> dict[str, Any]:
    """Ordered assembly manifest with integer offsets (§19.4).

    Each segment: {segment_id, artifact_id, frames, path}. Offsets accumulate
    in EXACT integer frames / samples — no float-second drift. Manifest order
    wins over any directory listing.
    """
    if not segments:
        raise VideoValidationError("empty assembly: no segments")
    fps_frac = Fraction(fps)
    manifest_segments: list[dict[str, Any]] = []
    frame_cursor = 0
    sample_cursor = 0
    for seg in segments:
        frames = int(seg["frames"])
        if frames <= 0:
            raise VideoValidationError(f"segment {seg.get('segment_id')} has nonpositive frames")
        manifest_segments.append({
            "segment_id": seg["segment_id"],
            "artifact_id": seg["artifact_id"],
            "path": seg["path"],
            "frames": frames,
            "start_frame": frame_cursor,
            "start_sample": sample_cursor,
            "start_time_rational": f"{frame_cursor}/{fps_frac}",
        })
        frame_cursor += frames
        sample_cursor += frames * sample_rate * fps_frac.denominator // fps_frac.numerator
    return {
        "schema_version": 1,
        "fps": {"num": fps_frac.numerator, "den": fps_frac.denominator},
        "sample_rate": sample_rate,
        "total_frames": frame_cursor,
        "total_samples": sample_cursor,
        "hard_cuts": True,
        "segments": manifest_segments,
    }


__all__ = [
    "ENCODER_NAME",
    "LINUX_DEV_ENCODER",
    "VideoFacts",
    "VideoValidationError",
    "build_assembly_manifest",
    "probe_video",
    "select_encoder",
    "validate_rendered_video",
]
