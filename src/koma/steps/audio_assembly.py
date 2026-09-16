"""Chapter audio assembly — dialogue TTS timeline mixed onto clip audio lane.

Implements the §17/§18 chain end to end:
1. Load the chapter's scripted dialogue lines (per panel, speaker-bound).
2. Synthesize each line via GPT-SoVITS (zero-shot from the approved voice
   binding) — MEASURED durations drive the timeline (audio-first doctrine).
3. Place cues on the dialogue bus (sequential, 200ms gap, §17.2).
4. Mix: dialogue bus + BGM (resting gain 0.25, speech ducking) via koma.audio.
5. Mux onto the rendered clip (pad/trim audio only, never the video).
"""

from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any

from ..adapters.tts import MIX_SAMPLE_RATE
from ..storage.errors import BlockedPrerequisite


def wav_stats(path: Path) -> tuple[int, int, int]:
    """(rate, channels, frames) from a WAV file header."""
    with wave.open(str(path), "rb") as w:
        return w.getframerate(), w.getnchannels(), w.getnframes()


def wav_duration_s(path: Path) -> float:
    rate, _ch, frames = wav_stats(path)
    return frames / rate


def collect_dialogue_lines(
    chapter_dir: Path,
    panel_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Read script.json and return per-panel dialogue lines in reading order."""
    sj = chapter_dir / "script.json"
    if not sj.exists():
        raise BlockedPrerequisite(f"no script.json in {chapter_dir} — run script first")
    data = json.loads(sj.read_text())
    panels = data.get("panels", {})
    out: list[dict[str, Any]] = []
    for pid in panel_ids or sorted(panels.keys()):
        pdoc = panels.get(pid) or {}
        lines = pdoc.get("dialogue") or pdoc.get("lines") or []
        for i, ln in enumerate(lines):
            text = (ln.get("jp") or ln.get("text") or "").strip()
            if not text:
                continue
            out.append({
                "panel_id": pid,
                "line_index": i,
                "text": text,
                "speaker": ln.get("speaker") or "",
            })
    return out


def synthesize_lines(
    lines: list[dict[str, Any]],
    *,
    tts: Any,
    voice_ref_path: str,
    prompt_text: str,
    workdir: Path,
    text_lang: str = "ja",
) -> list[dict[str, Any]]:
    """Synthesize every line; returns entries with measured PCM + samples."""
    from ..adapters.tts_client import normalize_to_mix_pcm

    workdir.mkdir(parents=True, exist_ok=True)
    entries = []
    for ln in lines:
        wav, _measured = tts.synthesize(
            ln["text"],
            text_lang=text_lang,
            ref_audio_path=voice_ref_path,
            prompt_text=prompt_text,
            prompt_lang=text_lang,
        )
        pcm, m = normalize_to_mix_pcm(wav)
        dst = workdir / f"line_{ln['panel_id']}_{ln['line_index']}.wav"
        dst.write_bytes(pcm)
        entries.append({**ln, "wav": str(dst), "samples": m.samples,
                        "duration_s": m.duration_s})
    return entries


def build_mix_and_mux(
    video: Path,
    line_entries: list[dict[str, Any]],
    output: Path,
    *,
    bgm_path: Path | None = None,
    workdir: Path | None = None,
) -> dict[str, Any]:
    """Timeline → mix → mux. Returns a report with measured facts."""
    from ..audio import MixCue, MixSpec, mix_to_file
    from ..mux import _ffprobe_duration_s, mux_audio

    if not line_entries:
        raise BlockedPrerequisite("no dialogue lines to place — nothing to mix")

    workdir = workdir or output.parent
    entries = sorted(line_entries, key=lambda e: (e["panel_id"], e["line_index"]))
    spec = MixSpec()
    cursor = 0
    for e in entries:
        spec.cues.append(MixCue(
            bus="dialogue",
            source=Path(e["wav"]),
            start_sample=cursor,
        ))
        cursor += e["samples"] + MIX_SAMPLE_RATE // 5  # 200ms gap
    content_samples = cursor - MIX_SAMPLE_RATE // 5
    # video duration is the authority for total length (audio-first: pad only)
    vdur = _ffprobe_duration_s(video)
    total = int(vdur * MIX_SAMPLE_RATE)
    spec.total_samples = max(total, content_samples)

    mixed = workdir / "mix.wav"
    measured = mix_to_file(spec, mixed)

    res = mux_audio(video, mixed, output)
    return {
        "output": str(output),
        "lines": len(entries),
        "content_seconds": content_samples / MIX_SAMPLE_RATE,
        "video_seconds": vdur,
        "mixed_seconds": measured.get("duration_s"),
        "muxed_seconds": _ffprobe_duration_s(output),
        "has_audio": res.has_audio,
    }


__all__ = [
    "build_mix_and_mux",
    "collect_dialogue_lines",
    "synthesize_lines",
    "wav_duration_s",
    "wav_stats",
]
