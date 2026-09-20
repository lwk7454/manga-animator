"""R7 — shot-based audio/subtitle/assembly (PLAN §R7, audio doctrine §16.4).

Per shot+ratio, the ACCEPTED motion candidate is the video lane. Dialogue
from the story beats drives the TTS lane; the mix pads to the video
duration (never trims/speed-adjusts video); subtitles render from the
same dialogue lines at measured times.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .log import get_logger
from .storage.database import Database
from .storage.errors import BlockedPrerequisite, NotFound

log = get_logger("audio.assembly")


class AssemblyService:
    def __init__(self, db: Database, tts_base: str | None = None):
        self.db = db
        self.tts_base = tts_base

    # ── inputs assembly ───────────────────────────────────────────────────
    def shot_inputs(self, series_id: str, shot_revision_id: str,
                    ratio: str) -> dict[str, Any]:
        """Resolve accepted candidate + beat dialogue lines for one shot.

        ``series_id`` may be empty: it is then resolved from the shot row
        (shot ids are unique across series), so web callers don't need it."""
        with self.db.transaction() as conn:
            if not series_id:
                r = conn.execute(
                    "SELECT series_id FROM shot_plan_revisions WHERE id = ?",
                    (shot_revision_id,),
                ).fetchone()
                if r is None:
                    raise NotFound(f"shot revision {shot_revision_id!r}")
                series_id = r["series_id"]
            cand = conn.execute(
                "SELECT c.id AS candidate_id, c.artifact_id, a.relative_path,"
                " a.sha256 FROM shot_candidates c"
                " JOIN artifacts a ON a.id = c.artifact_id"
                " WHERE c.shot_revision_id = ? AND c.ratio = ?"
                " AND c.acceptance = 'accepted'",
                (shot_revision_id, ratio),
            ).fetchone()
            if cand is None:
                raise BlockedPrerequisite(
                    f"no accepted candidate for shot {shot_revision_id} {ratio}")
            rev = conn.execute(
                "SELECT beat_coverage, source_panel_ids FROM shot_plan_revisions"
                " WHERE id = ?", (shot_revision_id,),
            ).fetchone()
            if rev is None:
                raise NotFound(f"shot revision {shot_revision_id!r}")
            beats = json.loads(rev["beat_coverage"])
            dialogue: list[dict[str, Any]] = []
            if beats:
                marks = ",".join("?" * len(beats))
                rows = conn.execute(
                    f"SELECT id, dialogue_line_ids FROM story_beats WHERE id IN ({marks})",
                    beats,
                ).fetchall()
                for r in rows:
                    for lid in json.loads(r["dialogue_line_ids"]):
                        dialogue.append({"line_id": lid, "beat_id": r["id"]})
        return {"video": {"artifact_id": cand["artifact_id"],
                          "relative_path": cand["relative_path"],
                          "sha256": cand["sha256"]},
                "dialogue": dialogue}

    # ── dialogue timeline ─────────────────────────────────────────────────
    def synthesize_dialogue(
        self, dialogue: list[dict[str, Any]], lines: dict[str, dict],
        *, ref_audio_path: str, prompt_text: str, prompt_lang: str = "en",
        text_lang: str = "ja", workdir: Path,
    ) -> dict[str, Any]:
        """TTS each line via api_v2; returns measured segments (§17.1).

        ``lines`` maps line_id → {"jp": ..., "en": ...}. Deterministic
        durations are measured from the produced WAVs (audio-first doctrine).
        """
        if not self.tts_base:
            raise RuntimeError("TTS lane not configured")
        import httpx
        workdir.mkdir(parents=True, exist_ok=True)
        timeline: list[dict[str, Any]] = []
        total = 0.0
        for item in dialogue:
            lid = item["line_id"]
            text = (lines.get(lid) or {}).get("jp", "").strip()
            if not text:
                continue
            payload = {
                "text": text, "text_lang": text_lang,
                "ref_audio_path": ref_audio_path,
                "prompt_text": prompt_text, "prompt_lang": prompt_lang,
            }
            r = httpx.post(f"{self.tts_base}/tts", json=payload,
                           timeout=120.0, trust_env=False)
            if r.status_code != 200:
                raise RuntimeError(f"tts failed ({r.status_code}): {r.text[:200]}")
            path = workdir / f"{lid.replace(':', '_').replace('/', '_')}.wav"
            path.write_bytes(r.content)
            seconds = _wav_seconds(path)
            timeline.append({"line_id": lid, "path": str(path),
                             "start": total, "seconds": seconds})
            total += seconds
        return {"timeline": timeline, "dialogue_seconds": total}

    # ── mix + mux (audio pads to video; video is authority) ───────────────
    MIX_SAMPLE_RATE = 48_000

    def build_mix_cues(self, timeline: list[dict[str, Any]],
                       bgm_path: str | None = None) -> list[dict[str, Any]]:
        """Dialogue cues at measured starts; optional BGM cue on its own bus."""
        rate = self.MIX_SAMPLE_RATE
        cues = [{"bus": "dialogue", "source": t["path"],
                 "start_sample": round(t["start"] * rate)}
                for t in timeline]
        if bgm_path:
            cues.append({"bus": "bgm", "source": bgm_path, "start_sample": 0})
        return cues

    def mux(self, cues: list[dict[str, Any]], video_path: Path,
            video_seconds: float, output: Path) -> dict[str, Any]:
        """Render the dialogue+BGM mix padded to the video duration, then mux
        onto a copy of the video stream (audio is the pad side; §16.4)."""
        from .audio import MIX_SAMPLE_RATE, MixCue, MixSpec, mix_to_file
        from .mux import mux_audio
        workdir = output.parent
        workdir.mkdir(parents=True, exist_ok=True)
        spec = MixSpec(
            cues=[MixCue(bus=c["bus"], source=Path(c["source"]),
                         start_sample=c["start_sample"])
                  for c in cues],
            total_samples=round(video_seconds * MIX_SAMPLE_RATE),
        )
        mixed = workdir / "dialogue_mix.wav"
        mix_to_file(spec, mixed)
        result = mux_audio(video_path, mixed, output)
        return {"output": str(result.output),
                "video_duration_s": result.video_duration_s,
                "audio_duration_s": result.audio_duration_s,
                "has_audio": result.has_audio}


def _wav_seconds(path: Path) -> float:
    import wave
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


__all__ = ["AssemblyService"]
