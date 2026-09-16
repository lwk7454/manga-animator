"""Audio mixing — sample-resolved buses, ducking, silence padding (§18.1).

Real ffmpeg filtergraph implementation of the seam. Rules tests verify:
- BGM applies resting gain 0.25; the BGM bus is NEVER re-amplified afterward
  (no loudness normalization that would undo the 0.25 requirement).
- Dialogue/SFX/ambience buses build from sample-resolved cues.
- Ducking: BGM is sidechain-compressed by the dialogue bus (bounded ratio).
- Final silence padding uses the EXACT validated video duration; content
  beyond target is REJECTED, never truncated speech; never ``-shortest``.
- Lossless PCM mix artifact before delivery encoding.
- argv arrays only — no shell interpolation of captions/paths.

Ducking calibration on real audio is hardware acceptance (§24); offline
tests use synthetic tones/envelopes and measure with tolerances (§18.1).
"""

from __future__ import annotations

import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters.tts import MIX_SAMPLE_RATE
from .log import get_logger

log = get_logger("audio.mixer")

BGM_RESTING_GAIN = 0.25
DUCK_THRESHOLD_LINEAR = 0.126  # -18 dBFS
DUCK_RATIO = 6.0


class MixerError(RuntimeError):
    pass


@dataclass
class MixCue:
    """One sample-resolved cue on a bus."""

    bus: str  # dialogue | sfx | ambience | bgm
    source: Path  # lossless PCM (48k stereo) input
    start_sample: int
    gain: float = 1.0
    trim_end_sample: int | None = None  # finite trim (BGM loops never extend)


@dataclass
class MixSpec:
    cues: list[MixCue] = field(default_factory=list)
    total_samples: int = 0  # exact ceiling from the validated video plan


def _seconds(samples: int) -> str:
    return f"{samples / MIX_SAMPLE_RATE:.6f}"


def build_filtergraph(spec: MixSpec) -> tuple[list[str], int]:
    """Construct the ffmpeg -filter_complex graph; returns (parts, n_cues).

    Layout: per-cue format→gain→(trim)→adelay; sum per bus; BGM gets resting
    gain 0.25 and is ducked by the dialogue bus via sidechaincompress; final
    sum is [out]. Zero-cue specs produce anullsrc silence.
    """
    if spec.total_samples <= 0:
        raise MixerError("mix duration must be positive (validated video plan)")
    parts: list[str] = []
    dialogue: list[str] = []
    bgm: list[str] = []
    other: list[str] = []
    for i, cue in enumerate(spec.cues):
        delay_ms = cue.start_sample * 1000.0 / MIX_SAMPLE_RATE
        trim = ""
        if cue.trim_end_sample is not None and cue.trim_end_sample > cue.start_sample:
            dur = (cue.trim_end_sample - cue.start_sample) / MIX_SAMPLE_RATE
            trim = f",atrim=0:{dur:.6f}"
        parts.append(
            f"[{i}:a]aformat=sample_rates={MIX_SAMPLE_RATE}:channel_layouts=stereo,"
            f"volume={cue.gain}{trim},"
            f"adelay={delay_ms:.3f}|{delay_ms:.3f}[a{i}]"
        )
        if cue.bus == "dialogue":
            dialogue.append(f"[a{i}]")
        elif cue.bus == "bgm":
            bgm.append(f"[a{i}]")
        else:
            other.append(f"[a{i}]")

    def _sum(labels: list[str], tag: str) -> str | None:
        if not labels:
            return None
        if len(labels) == 1:
            parts.append(f"{labels[0]}anull[{tag}]")
            return f"[{tag}]"
        parts.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0[{tag}]")
        return f"[{tag}]"

    d = _sum(dialogue, "busd")
    b = _sum(bgm, "busb")
    o = _sum(other, "buso")

    final: list[str] = []
    if b and d:
        # A filter label can be consumed only ONCE: split the dialogue bus
        # into the sidechain path and the audible path (asplit).
        parts.append(f"{d}asplit=2[dsc][dout]")
        # sidechaincompress: [main][sidechain] — MAIN (BGM, resting-gained)
        # is compressed when the SIDECHAIN (dialogue) is loud (§18.1).
        parts.append(
            f"{b}volume={BGM_RESTING_GAIN}[bgmrest]"
        )
        parts.append(
            f"[bgmrest][dsc]sidechaincompress=threshold={DUCK_THRESHOLD_LINEAR:.6f}:"
            f"ratio={DUCK_RATIO}:attack=20:release=400[bgmduck]"
        )
        final.append("[bgmduck]")
        final.append("[dout]")
    else:
        if b:
            parts.append(f"{b}volume={BGM_RESTING_GAIN}[bgmrest]")
            final.append("[bgmrest]")
        if d:
            final.append(d)
    if o:
        final.append(o)
    if not final:
        parts.append(f"anullsrc=r={MIX_SAMPLE_RATE}:cl=stereo,atrim=0:"
                     f"{_seconds(spec.total_samples)},asetpts=N/SR/TB[out]")
        return parts, 0
    if len(final) == 1:
        parts.append(f"{final[0]}apad=whole_dur={_seconds(spec.total_samples)},"
                     f"atrim=0:{_seconds(spec.total_samples)}[out]")
    else:
        parts.append(f"{''.join(final)}amix=inputs={len(final)}:normalize=0,"
                     f"apad=whole_dur={_seconds(spec.total_samples)},"
                     f"atrim=0:{_seconds(spec.total_samples)},"
                     f"alimiter=limit=0.95[out]")
    return parts, len(spec.cues)


def mix_to_file(spec: MixSpec, out_path: Path, *, ffmpeg: str = "ffmpeg") -> dict[str, Any]:
    """Render the mix losslessly (16-bit PCM WAV). Returns measured metadata."""
    graph, _ = build_filtergraph(spec)
    cmd: list[str] = [ffmpeg, "-y"]
    for cue in spec.cues:
        cmd += ["-i", str(cue.source)]
    if not spec.cues:
        cmd += ["-f", "lavfi", "-i",
                f"anullsrc=r={MIX_SAMPLE_RATE}:cl=stereo"]
        cmd += ["-t", _seconds(spec.total_samples)]
    cmd += ["-filter_complex", ";".join(graph), "-map", "[out]",
            "-ar", str(MIX_SAMPLE_RATE), "-ac", "2", "-c:a", "pcm_s16le",
            str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise MixerError(f"ffmpeg mix failed: {proc.stderr[-400:]!r}")
    return measure_wav(out_path)


def measure_wav(path: Path) -> dict[str, Any]:
    """Measured metadata from the decoded container header (never trust text)."""
    with wave.open(str(path), "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate()
        channels = w.getnchannels()
    return {"samples": frames, "sample_rate": rate, "channels": channels,
            "duration_s": frames / rate}


def silence_pad_to_samples(
    mix_path: Path, target_samples: int, out_path: Path, *, ffmpeg: str = "ffmpeg"
) -> dict[str, Any]:
    """Pad with silence to the EXACT sample count (§18.1: never -shortest).

    Content beyond the validated video duration is REJECTED — never truncate
    speech to fit.
    """
    info = measure_wav(mix_path)
    if info["samples"] > target_samples:
        raise MixerError(
            f"mix content ({info['samples']} samples) exceeds the validated video "
            f"duration ({target_samples}) — rejecting instead of truncating speech"
        )
    pad_s = (target_samples - info["samples"]) / MIX_SAMPLE_RATE
    cmd = [ffmpeg, "-y", "-i", str(mix_path),
           "-af", f"apad=pad_dur={pad_s:.6f}",
           "-ar", str(MIX_SAMPLE_RATE), "-ac", "2", "-c:a", "pcm_s16le",
           str(out_path)]
    proc = subprocess.run(cmd, capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise MixerError(f"ffmpeg pad failed: {proc.stderr[-300:]!r}")
    return measure_wav(out_path)


__all__ = [
    "BGM_RESTING_GAIN",
    "MixCue",
    "MixSpec",
    "MixerError",
    "build_filtergraph",
    "measure_wav",
    "mix_to_file",
    "silence_pad_to_samples",
]
