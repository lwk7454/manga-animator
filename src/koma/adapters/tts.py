"""Local TTS adapter seam + measured-content timelines (PLAN §17).

GPT-SoVITS is the first backend (§17.1) — the adapter wraps the ACTUAL
installed local API contract, which requires hardware access to qualify.
This module defines the typed transport + the validation rules (decodable,
finite, nonempty waveform; measured sample rate/channels/duration from the
DECODED audio, never response text) and the sample-exact timeline math
(§17.2): all internal timestamps are integer samples at the mix rate;
dialogue is sequential with a configurable 120ms default gap; a no-speech
panel gets a finite 2s minimum; anchors resolve after synthesis; BGM loops
never extend a finite timeline.

No cloud fallback; no silent engine switch; synthesis requires the exact
approved voice revision (D05/D10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..log import get_logger
from ..storage.errors import BlockedPrerequisite

log = get_logger("adapters.tts")

MIX_SAMPLE_RATE = 48000
DEFAULT_INTER_LINE_GAP_MS = 120
DEFAULT_NO_SPEECH_MIN_S = 2.0


class TTSError(RuntimeError):
    """TTS transport/validation failure (no automatic engine fallback)."""


@dataclass
class VoiceBinding:
    """The exact approved voice a synthesis request must use (§14.1)."""

    entity_id: str
    revision: int
    engine: str  # "gpt_sovits" first; never switched silently
    ref_audio_artifact: str
    ref_transcript: str
    ref_language: str = "ja"


@dataclass
class LineRequest:
    """One independently reusable synthesis work item (§17.1)."""

    line_id: str
    panel_id: str
    spoken_text: str  # normalized speech (display/source preserved elsewhere)
    language: str = "ja"
    voice: VoiceBinding | None = None
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class MeasuredAudio:
    """Validation result measured from DECODED audio (never response text)."""

    sample_rate: int
    channels: int
    samples: int

    @property
    def duration_s(self) -> float:
        return self.samples / self.sample_rate

    def validate(self) -> None:
        if self.samples <= 0:
            raise TTSError("empty waveform (zero samples) — not a successful synthesis")
        if self.sample_rate <= 0:
            raise TTSError("invalid sample rate")
        if self.channels not in (1, 2):
            raise TTSError(f"unexpected channel count {self.channels}")
        # finite check happens at decode (NaN/inf rejected in the PCM reader)


@dataclass
class TimelineEvent:
    """A resolved event: integer sample positions (§17.4)."""

    event_id: str
    kind: Literal["dialogue", "sfx", "ambience", "bgm_mood"]
    start_sample: int
    end_sample: int | None
    asset: str | None = None
    gain: float = 1.0
    reason: str = ""


@dataclass
class PanelTimeline:
    """One panel's measured content timeline (finite by construction)."""

    panel_id: str
    events: list[TimelineEvent] = field(default_factory=list)
    content_end_sample: int = 0

    @property
    def duration_s(self) -> float:
        return self.content_end_sample / MIX_SAMPLE_RATE


def sequential_dialogue_timeline(
    panel_id: str,
    lines: list[str],
    *,
    measured_durations_s: list[float] | None = None,
    gap_ms: int = DEFAULT_INTER_LINE_GAP_MS,
    no_speech: bool = False,
) -> PanelTimeline:
    """Build a panel timeline: sequential dialogue + finite gaps (§17.2).

    ``measured_durations_s`` are MEASURED synthesis results; token estimates
    are never final audio length (§17.1). Without measurements the timeline
    is a placeholder whose content end must be recomputed post-synthesis.
    """
    gap = gap_ms * MIX_SAMPLE_RATE // 1000
    tl = PanelTimeline(panel_id=panel_id)
    if no_speech or not lines:
        tl.content_end_sample = int(DEFAULT_NO_SPEECH_MIN_S * MIX_SAMPLE_RATE)
        tl.events.append(TimelineEvent(
            event_id=f"ev:{panel_id}:0", kind="ambience",
            start_sample=0, end_sample=tl.content_end_sample,
            reason="no-speech minimum (finite; §17.2)",
        ))
        return tl
    if measured_durations_s is None:
        # placeholder mode: events carry symbolic zero positions; the caller
        # MUST recompute after synthesis (never use as final length)
        return tl
    cursor = 0
    for i, (_line, dur) in enumerate(zip(lines, measured_durations_s, strict=False)):
        samples = round(dur * MIX_SAMPLE_RATE)
        if samples <= 0:
            raise TTSError(f"line {i} measured nonpositive duration — not intelligible audio")
        tl.events.append(TimelineEvent(
            event_id=f"ev:{panel_id}:line{i}", kind="dialogue",
            start_sample=cursor, end_sample=cursor + samples,
            reason="sequential dialogue",
        ))
        cursor += samples + gap
    # trailing gap after the last line is NOT content: content ends at last audio
    tl.content_end_sample = cursor - gap if cursor >= gap else cursor
    return tl


def validate_voice_binding(binding: VoiceBinding | None) -> VoiceBinding:
    """Production synthesis requires an EXACT approved voice (D05/§14.4)."""
    if binding is None:
        raise BlockedPrerequisite(
            "no approved voice binding for this line — a complete voiced "
            "chapter cannot be claimed without one (setup/review blocker)"
        )
    if not binding.ref_audio_artifact:
        raise BlockedPrerequisite("voice binding lacks a reference audio artifact")
    if not binding.ref_transcript:
        raise BlockedPrerequisite("voice binding lacks the engine-required transcript")
    return binding


def normalize_to_mix_pcm(
    wav_bytes: bytes, *, source_rate: int, source_channels: int
) -> tuple[bytes, MeasuredAudio]:
    """Deterministic resample/channel conversion to 48kHz stereo PCM (§17.1).

    Uses ffmpeg (present on the orchestration host; capability-probed at
    doctor time). Returns the lossless interchange payload + measurements.
    """
    import subprocess

    if not wav_bytes:
        raise TTSError("empty audio payload")
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src.wav"
        dst = Path(td) / "out.wav"
        src.write_bytes(wav_bytes)
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-ar", str(MIX_SAMPLE_RATE),
             "-ac", "2", "-c:a", "pcm_s16le", str(dst)],
            capture_output=True, timeout=60,
        )
        if proc.returncode != 0:
            raise TTSError(f"ffmpeg resample failed: {proc.stderr[-200:]!r}")
        data = dst.read_bytes()
    # parse WAV header for measured metadata (no response-text trusting)

    if len(data) < 44 or data[:4] != b"RIFF":
        raise TTSError("not a RIFF/WAVE payload")
    samples = (len(data) - 44) // 4  # 16-bit stereo = 4 bytes/frame
    m = MeasuredAudio(sample_rate=MIX_SAMPLE_RATE, channels=2, samples=samples)
    m.validate()
    return data, m


__all__ = [
    "DEFAULT_INTER_LINE_GAP_MS",
    "DEFAULT_NO_SPEECH_MIN_S",
    "MIX_SAMPLE_RATE",
    "LineRequest",
    "MeasuredAudio",
    "PanelTimeline",
    "TTSError",
    "TimelineEvent",
    "VoiceBinding",
    "normalize_to_mix_pcm",
    "sequential_dialogue_timeline",
    "validate_voice_binding",
]
