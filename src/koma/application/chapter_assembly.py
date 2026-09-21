"""Chapter assembly across accepted shots (G6)."""

from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from ..storage.database import Database
from ..storage.errors import BlockedPrerequisite, NotFound
from ..video import build_assembly_manifest


def chapter_shot_order(db: Database, chapter_id: str) -> list[dict[str, Any]]:
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM shot_plan_revisions WHERE chapter_id=? ORDER BY created_at, id",
            (chapter_id,),
        ).fetchall()
    indexed = []
    with db.transaction() as conn:
        for shot in (dict(r) for r in rows):
            beats = json.loads(shot["beat_coverage"])
            if not beats:
                raise BlockedPrerequisite(f"shot {shot['id']} covers no beats")
            beat = conn.execute(
                "SELECT beat_index FROM story_beats WHERE id=?", (beats[0],)
            ).fetchone()
            if beat is None:
                raise BlockedPrerequisite(f"missing beat {beats[0]}")
            indexed.append((beat["beat_index"], shot))
    indexed.sort(key=lambda item: (item[0], item[1]["id"]))
    return [shot for _, shot in indexed]


def offset_subtitles(tracks: list[tuple[float, str]]) -> str:
    from ..subtitles import _fmt_srt_ts

    entries = []
    index = 1
    for offset, body in tracks:
        for block in body.strip().split("\n\n"):
            lines = block.splitlines()
            if len(lines) < 2 or "-->" not in lines[1]:
                continue
            start_s, end_s = _parse_range(lines[1])
            text = "\n".join(lines[2:])
            entries.append(
                f"{index}\n{_fmt_srt_ts(start_s + offset)} --> {_fmt_srt_ts(end_s + offset)}\n{text}\n"
            )
            index += 1
    return "\n".join(entries)


def _parse_range(line: str) -> tuple[float, float]:
    left, _, right = line.partition("-->")
    return _parse_ts(left.strip()), _parse_ts(right.strip())


def _parse_ts(value: str) -> float:
    hh, mm, rest = value.replace(",", ".").split(":")
    return int(hh) * 3600 + int(mm) * 60 + float(rest)


def assemble_chapter(
    db: Database,
    chapter_id: str,
    ratio: str,
    *,
    clips: list[dict[str, Any]],
    output_dir: Path,
    fps: int = 24,
) -> dict[str, Any]:
    """Concatenate accepted per-shot deliveries in beat order (hard cuts)."""
    if ratio not in {"16:9", "9:16"}:
        raise BlockedPrerequisite(f"unsupported delivery ratio {ratio}")
    order = chapter_shot_order(db, chapter_id)
    if not order:
        raise NotFound(f"no shot plan revisions for chapter {chapter_id}")
    ids = [shot["id"] for shot in order]
    by_id = {clip["shot_revision_id"]: clip for clip in clips}
    missing = [sid for sid in ids if sid not in by_id]
    if missing:
        raise BlockedPrerequisite(f"missing assembled clips for shots {missing}")
    extra = sorted(set(by_id) - set(ids))
    if extra:
        raise BlockedPrerequisite(f"clips for unknown shots {extra}")
    ordered = [by_id[sid] for sid in ids]
    dest = Path(output_dir)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"chapter_{ratio.replace(':', 'x')}.mp4"
    list_path = dest / "concat.txt"
    lines = []
    for clip in ordered:
        path = Path(clip["video_path"])
        if not path.is_file():
            raise NotFound(f"assembled clip missing: {path}")
        lines.append(f"file '{path.resolve().as_posix()}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i",
            str(list_path), "-c", "copy", str(out),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    offset = 0.0
    tracks: list[tuple[float, str]] = []
    segments: list[dict[str, Any]] = []
    for clip in ordered:
        srt = clip.get("srt")
        if srt:
            tracks.append((offset, Path(srt).read_text(encoding="utf-8")))
        duration = float(clip["duration_s"])
        frames = int(clip.get("frames") or round(duration * fps))
        segments.append(
            {
                "segment_id": clip["shot_revision_id"],
                "artifact_id": clip.get("artifact_id") or clip["shot_revision_id"],
                "path": str(clip["video_path"]),
                "frames": frames,
            }
        )
        offset += duration
    srt_body = offset_subtitles(tracks)
    srt_path = dest / "chapter.srt"
    if srt_body:
        body = srt_body if srt_body.endswith("\n") else srt_body + "\n"
        srt_path.write_text(body, encoding="utf-8")
    manifest = build_assembly_manifest(segments, fps=Fraction(fps, 1))
    (dest / "assembly_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return {
        "output": str(out),
        "subtitles": str(srt_path) if srt_body else None,
        "manifest": manifest,
        "shot_order": ids,
        "duration_s": offset,
        "ratio": ratio,
    }


__all__ = ["assemble_chapter", "chapter_shot_order", "offset_subtitles"]
