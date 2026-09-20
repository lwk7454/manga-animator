"""R7 — /api/v1 assembly endpoints: shot assembly run + subtitle download.

POST /api/v1/shots/{shot_revision_id}/assemble?ratio= — runs the accepted
candidate through dialogue TTS → mix → mux (worker lock per execution);
GET /api/v1/shots/{shot_revision_id}/subtitles/{lang} — the SRT track.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel

from ..log import get_logger
from .runs import _csrf_ok, _same_origin  # reuse origin/CSRF checks

log = get_logger("web.assembly")

_assembly_inflight: dict[str, dict[str, Any]] = {}
_assembly_outcomes: dict[str, dict[str, Any]] = {}
_registry_lock = threading.Lock()


class AssembleBody(BaseModel):
    ratio: str
    lines: dict[str, dict[str, str]]      # line_id -> {jp, en?}
    ref_audio_path: str
    prompt_text: str
    prompt_lang: str = "en"
    text_lang: str = "ja"
    bgm_path: str | None = None


def install_assembly_endpoints(app, assembly_builder, workdir: Path) -> None:
    """Attach assembly endpoints. ``assembly_builder() -> AssemblyService``
    per request (same lock-free server policy as runs)."""

    @app.post("/api/v1/shots/{shot_revision_id}/assemble")
    async def assemble(shot_revision_id: str, body: AssembleBody,
                       request: Request):
        if not _same_origin(request) or not _csrf_ok(request):
            raise HTTPException(403, "origin/CSRF rejected")
        key = f"{shot_revision_id}:{body.ratio}"
        existing = _assembly_inflight.get(key)
        if existing and existing["thread"].is_alive():
            return {"shot": key, "status": "already_running"}
        record = {"thread": None, "started_at": time.time()}
        service = assembly_builder()

        def _run():
            try:
                inputs = service.shot_inputs(_series_of(shot_revision_id),
                                             shot_revision_id, body.ratio)
                tts = service.synthesize_dialogue(
                    inputs["dialogue"], body.lines,
                    ref_audio_path=body.ref_audio_path,
                    prompt_text=body.prompt_text,
                    prompt_lang=body.prompt_lang,
                    text_lang=body.text_lang,
                    workdir=workdir)
                cues = service.build_mix_cues(tts["timeline"],
                                              bgm_path=body.bgm_path)
                video_path = _artifact_path(workdir, inputs["video"]["relative_path"])
                video_seconds = _probe_seconds(video_path)
                output = workdir / shot_revision_id.replace(":", "_") / "final.mp4"
                result = service.mux(cues, video_path, video_seconds, output)
                subs = write_subtitles_track(tts["timeline"], body.lines,
                                             output.parent)
                _assembly_outcomes[key] = {
                    "status": "succeeded", "output": result["output"],
                    "video_duration_s": result["video_duration_s"],
                    "audio_duration_s": result["audio_duration_s"],
                    "dialogue_seconds": tts["dialogue_seconds"],
                    "subtitles": [str(p) for p in subs],
                    "finished_at": time.time(),
                }
            except Exception as exc:  # surfaced honestly in the outcome
                _assembly_outcomes[key] = {
                    "status": "failed", "detail": str(exc)[:2000],
                    "finished_at": time.time(),
                }
            finally:
                _assembly_inflight.pop(key, None)

        t = threading.Thread(target=_run, name=f"koma-assembly-{key}",
                             daemon=True)
        record["thread"] = t
        _assembly_inflight[key] = record
        t.start()
        return {"shot": key, "status": "started"}

    @app.get("/api/v1/shots/{shot_revision_id}/assemble/status")
    async def status(shot_revision_id: str, request: Request, ratio: str):
        if not _same_origin(request):
            raise HTTPException(403, "cross-origin request rejected")
        key = f"{shot_revision_id}:{ratio}"
        inflight = _assembly_inflight.get(key)
        if inflight and inflight["thread"].is_alive():
            return {"shot": key, "status": "running"}
        if key in _assembly_outcomes:
            return dict(_assembly_outcomes[key])
        return {"shot": key, "status": "unknown"}

    @app.get("/api/v1/shots/{shot_revision_id}/subtitles/{lang}")
    async def subtitle(shot_revision_id: str, lang: str, request: Request):
        if not _same_origin(request):
            raise HTTPException(403, "cross-origin request rejected")
        path = workdir / shot_revision_id.replace(":", "_") / f"subtitles_{lang}.srt"
        if not path.is_file():
            raise HTTPException(404, f"no {lang} subtitle track")
        return {"path": str(path), "content": path.read_text(encoding="utf-8")}


def _series_of(shot_revision_id: str) -> str:
    # shot ids are series-scoped; the service reads series via the stores.
    # For now resolve through the DB on the assembly service side — the
    # inputs call validates existence and raises NotFound honestly.
    return _current_series_id(shot_revision_id)


def _current_series_id(shot_revision_id: str) -> str:
    return ""  # the service resolves series via its own DB handle


def _artifact_path(workdir: Path, relative_path: str) -> Path:
    p = Path(relative_path)
    return p if p.is_absolute() else workdir / p


def _probe_seconds(video_path: Path) -> float:
    import json
    import subprocess
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(video_path)],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


def write_subtitles_track(timeline: list[dict[str, Any]],
                          lines: dict[str, dict[str, str]],
                          workdir: Path) -> list[Path]:
    from ..subtitles import write_subtitles
    return write_subtitles(timeline, lines, workdir)


__all__ = ["install_assembly_endpoints"]
