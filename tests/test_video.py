"""P12 offline — video validation, encoder policy, assembly manifest (§19).

Real ffmpeg where available: synthetic clips are generated, probed and
validated. Glob-order and decimal-drift regressions are pinned by tests.
"""

from __future__ import annotations

import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from koma.video import (
    VideoValidationError,
    build_assembly_manifest,
    probe_video,
    select_encoder,
    validate_rendered_video,
)

ffmpeg = shutil.which("ffmpeg")
ffprobe = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(ffmpeg is None or ffprobe is None, reason="ffmpeg/ffprobe")


@pytest.fixture()
def clip(tmp_path: Path) -> Path:
    """A 1s 320x240 24fps testsrc clip with audio (440Hz)."""
    out = tmp_path / "clip.mp4"
    proc = subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=24",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)],
        capture_output=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-300:]
    return out


def test_probe_measures_facts(clip: Path):
    facts = probe_video(clip)
    assert (facts.width, facts.height) == (320, 240)
    assert facts.fps == Fraction(24, 1)
    assert facts.frames == 24
    assert facts.has_audio


def test_probe_missing_file_raises(tmp_path: Path):
    with pytest.raises(VideoValidationError, match="missing"):
        probe_video(tmp_path / "nope.mp4")


def test_probe_rejects_non_media(tmp_path: Path):
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not a video" * 100)
    with pytest.raises(VideoValidationError):
        probe_video(junk)


def test_validate_accepts_correct_render(clip: Path):
    facts = validate_rendered_video(
        clip, expect_width=320, expect_height=240,
        expect_fps=Fraction(24, 1), min_frames=24,
    )
    assert facts.frames == 24


def test_validate_rejects_wrong_dimensions(clip: Path, tmp_path: Path):
    with pytest.raises(VideoValidationError, match="dimensions"):
        validate_rendered_video(clip, expect_width=1920, expect_height=1080,
                                expect_fps=Fraction(24, 1), min_frames=1)


def test_validate_rejects_short_render(clip: Path):
    """Frame accounting never rounds down: < required fails promotion."""
    with pytest.raises(VideoValidationError, match="frames"):
        validate_rendered_video(
            clip, expect_width=320, expect_height=240,
            expect_fps=Fraction(24, 1), min_frames=25,
        )


def test_validate_rejects_missing_audio(clip: Path):
    """A render missing its expected audio stream cannot pass silently."""
    import subprocess

    silent = clip.parent / "silent.mp4"
    r = subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=24",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(silent)],
        capture_output=True, timeout=120,
    )
    assert r.returncode == 0
    with pytest.raises(VideoValidationError, match="audio"):
        validate_rendered_video(silent, expect_width=320, expect_height=240,
                                expect_fps=Fraction(24, 1), min_frames=1)


# ── encoder policy (§19.3) ───────────────────────────────────────────────────


def test_encoder_platform_defaults():
    assert select_encoder(platform="darwin") == "h264_videotoolbox"
    assert select_encoder(platform="linux") == "libx264"


def test_explicit_encoder_wins_and_is_recordable():
    assert select_encoder(platform="darwin", explicit="libx264") == "libx264"


# ── assembly manifest (§19.4) ────────────────────────────────────────────────


def test_manifest_offsets_are_exact_integers():
    m = build_assembly_manifest(
        [
            {"segment_id": "s1", "artifact_id": "a1", "path": "x.mp4", "frames": 39},
            {"segment_id": "s2", "artifact_id": "a2", "path": "y.mp4", "frames": 56},
            {"segment_id": "s3", "artifact_id": "a3", "path": "z.mp4", "frames": 5},
        ],
        fps=Fraction(24, 1),
    )
    assert m["total_frames"] == 100
    assert [s["start_frame"] for s in m["segments"]] == [0, 39, 95]
    # exact integer sample conversion: frames * 48000 * fps_den / fps_num
    assert m["segments"][1]["start_sample"] == 39 * 48000 // 24  # exact int, no drift
    assert m["hard_cuts"] is True


def test_manifest_rejects_nonpositive_frames():
    with pytest.raises(VideoValidationError, match="nonpositive"):
        build_assembly_manifest(
            [{"segment_id": "s", "artifact_id": "a", "path": "x", "frames": 0}],
            fps=Fraction(24, 1),
        )


def test_manifest_rejects_empty():
    with pytest.raises(VideoValidationError, match="empty"):
        build_assembly_manifest([], fps=Fraction(24, 1))


def test_manifest_preserves_given_order_not_sorted():
    """Manifest order wins — never sorted or globbed (§19.4)."""
    m = build_assembly_manifest(
        [
            {"segment_id": "zeta", "artifact_id": "a3", "path": "z", "frames": 5},
            {"segment_id": "alpha", "artifact_id": "a1", "path": "a", "frames": 7},
        ],
        fps=Fraction(24, 1),
    )
    assert [s["segment_id"] for s in m["segments"]] == ["zeta", "alpha"]
