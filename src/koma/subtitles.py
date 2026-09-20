"""R7 — subtitle renderer: SRT from the measured dialogue timeline.

Line text comes from the script facts (jp + optional en translation);
times come from the measured TTS durations — never estimated. One SRT
per language; jp is always rendered, en only when translations exist.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _fmt_srt_ts(seconds: float) -> str:
    ms = round(seconds * 1000)
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(timeline: list[dict[str, Any]],
              lines: dict[str, dict[str, str]],
              *, language: str = "jp") -> str:
    """One subtitle cue per dialogue line: start → start+duration."""
    entries: list[str] = []
    for t in timeline:
        lid = t["line_id"]
        text = (lines.get(lid) or {}).get(language, "").strip()
        if not text:
            continue
        start = t["start"]
        end = start + t["seconds"]
        entries.append(
            f"{len(entries) + 1}\n"
            f"{_fmt_srt_ts(start)} --> {_fmt_srt_ts(end)}\n"
            f"{text}\n"
        )
    return "\n".join(entries)


def write_subtitles(timeline: list[dict[str, Any]],
                    lines: dict[str, dict[str, str]],
                    workdir: Path,
                    *, languages: tuple[str, ...] = ("jp", "en")) -> list[Path]:
    """Render available subtitle tracks (jp always; en when translations exist)."""
    out: list[Path] = []
    for lang in languages:
        srt = build_srt(timeline, lines, language=lang)
        if not srt.strip():
            continue  # language has no content; never emit an empty track
        path = workdir / f"subtitles_{lang}.srt"
        path.write_text(srt, encoding="utf-8")
        out.append(path)
    return out


__all__ = ["build_srt", "write_subtitles"]
