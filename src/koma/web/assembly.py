"""Explicit assembly previews; production voice/script binding is not yet wired.

Durable preview status lives in SQLite. Each request writes immutable files in
its own shot/ratio/request directory. No preview advances production completion.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel

from ..r2v_probe import digest
from ..scheduler.ownership import OwnerBusy, OwnerLock
from ..storage.errors import BlockedPrerequisite, NotFound
from ..util import canonical_hash, new_id
from .runs import _csrf_ok, _same_origin


class AssembleBody(BaseModel):
    ratio: Literal["16:9", "9:16"]
    preview: bool = False
    lines: dict[str, dict[str, Any]]
    ref_audio_path: str
    prompt_text: str
    prompt_lang: str = "en"
    text_lang: str = "ja"
    bgm_path: str | None = None


def install_assembly_endpoints(app, assembly_builder, workdir: Path, *, owner_lock=None) -> None:
    root = Path(workdir).resolve()
    lock = owner_lock or OwnerLock()
    inflight = {}
    mutex = threading.Lock()

    def key(shot, ratio):
        return "assembly-preview:" + canonical_hash([shot, ratio])

    def get(service, shot, ratio):
        return json.loads(service.db.kv_get(key(shot, ratio)) or '{"status":"unknown"}')

    def put(service, shot, ratio, record):
        service.db.kv_set(key(shot, ratio), json.dumps(record))

    @app.post("/api/v1/shots/{shot_revision_id}/assemble")
    async def assemble(shot_revision_id: str, body: AssembleBody, request: Request):
        if not _same_origin(request) or not _csrf_ok(request):
            raise HTTPException(403, "origin/CSRF rejected")
        if not body.preview:
            raise HTTPException(
                422,
                "production assembly requires approved voice/script bindings; requ"
                "est an explicit preview",
            )
        service = assembly_builder()
        try:
            inputs = service.shot_inputs("", shot_revision_id, body.ratio)
            video = service.video_path(inputs)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (BlockedPrerequisite, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        fingerprint = canonical_hash([body.model_dump(), inputs])
        run_key = key(shot_revision_id, body.ratio)
        with mutex:
            prior = get(service, shot_revision_id, body.ratio)
            if run_key in inflight:
                if prior.get("fingerprint") != fingerprint:
                    raise HTTPException(409, "a different assembly preview is already running")
                return {"status": "already_running", "operation_id": prior["operation_id"]}
            if prior.get("status") == "succeeded" and prior.get("fingerprint") == fingerprint:
                output = Path(prior["output"])
                if output.is_file() and digest(output) == prior.get("output_sha256"):
                    return prior
            try:
                owner = lock.acquire("assembly preview", service.db.data_dir)
            except OwnerBusy as exc:
                raise HTTPException(409, str(exc)) from exc
            try:
                with service.db.transaction() as conn:
                    active = conn.execute(
                        "SELECT id FROM attempts WHERE state IN ('submitting','submission_"
                        "uncertain','running') LIMIT 1"
                    ).fetchone()
                    if active:
                        raise HTTPException(
                            409, "reconcile/drain remote generation before starting TTS"
                        )
                operation = new_id()
                target = (
                    root
                    / "assembly-previews"
                    / canonical_hash([shot_revision_id, body.ratio])
                    / operation
                )
                target.mkdir(parents=True)
                record = {
                    "status": "running",
                    "operation_id": operation,
                    "fingerprint": fingerprint,
                    "inputs": inputs,
                    "preview": True,
                    "production_complete": False,
                    "started_at": time.time(),
                }
                put(service, shot_revision_id, body.ratio, record)
            except BaseException:
                lock.release(owner)
                raise
            inflight[run_key] = operation

        def run():
            try:
                # Validate required source/translation content before spending GPU time.
                languages = tuple(dict.fromkeys(["jp", *inputs["subtitle_languages"]]))
                required = [
                    {"line_id": d["line_id"], "start": 0, "seconds": 1}
                    for d in inputs["dialogue"]
                    if not body.lines.get(d["line_id"], {}).get("no_speech", False)
                ]
                from ..subtitles import write_subtitles

                # Preflight into a separate directory; no placeholder tracks are delivered.
                write_subtitles(required, body.lines, target / "preflight", languages=languages)
                tts = service.synthesize_dialogue(
                    inputs["dialogue"],
                    body.lines,
                    ref_audio_path=body.ref_audio_path,
                    prompt_text=body.prompt_text,
                    prompt_lang=body.prompt_lang,
                    text_lang=body.text_lang,
                    workdir=target,
                )
                bgm = None
                if body.bgm_path:
                    bgm_file = (root / body.bgm_path).resolve()
                    if not bgm_file.is_relative_to(root) or not bgm_file.is_file():
                        raise BlockedPrerequisite(
                            "BGM must be an existing file inside the data root"
                        )
                    bgm = str(bgm_file)
                cues = service.build_mix_cues(tts["timeline"], bgm_path=bgm)
                result = service.mux(cues, video, _probe_seconds(video), target / "preview.mp4")
                subs = write_subtitles(tts["timeline"], body.lines, target, languages=languages)
                current = service.shot_inputs("", shot_revision_id, body.ratio)
                if current != inputs:
                    raise BlockedPrerequisite(
                        "accepted candidate/reference changed during assembly"
                    )
                record.update(
                    result,
                    status="succeeded",
                    dialogue_seconds=tts["dialogue_seconds"],
                    subtitles={p.stem.removeprefix("subtitles_"): str(p) for p in subs},
                    output_sha256=digest(Path(result["output"])),
                    finished_at=time.time(),
                )
                put(service, shot_revision_id, body.ratio, record)
            except Exception as exc:
                record.update(status="failed", detail=str(exc)[:2000], finished_at=time.time())
                put(service, shot_revision_id, body.ratio, record)
            finally:
                with mutex:
                    inflight.pop(run_key, None)
                lock.release(owner)
                service.db.close()

        thread = threading.Thread(target=run, name=f"koma-assembly-{operation}", daemon=True)
        try:
            thread.start()
        except BaseException:
            with mutex:
                inflight.pop(run_key, None)
            lock.release(owner)
            record.update(status="interrupted", detail="worker did not start")
            put(service, shot_revision_id, body.ratio, record)
            raise
        return {
            "status": "started",
            "operation_id": operation,
            "preview": True,
            "production_complete": False,
        }

    @app.get("/api/v1/shots/{shot_revision_id}/assemble/status")
    async def status(shot_revision_id: str, request: Request, ratio: Literal["16:9", "9:16"]):
        if not _same_origin(request):
            raise HTTPException(403, "origin rejected")
        service = assembly_builder()
        record = get(service, shot_revision_id, ratio)
        if record.get("status") == "running" and key(shot_revision_id, ratio) not in inflight:
            record = {
                **record,
                "status": "interrupted",
                "detail": "preview worker restarted; explicitly retry",
            }
        if record.get("status") == "succeeded":
            try:
                if service.shot_inputs("", shot_revision_id, ratio) != record["inputs"]:
                    raise BlockedPrerequisite("candidate changed")
            except (BlockedPrerequisite, NotFound) as exc:
                return {**record, "status": "stale", "detail": str(exc)}
        return record

    @app.get("/api/v1/shots/{shot_revision_id}/subtitles/{lang}")
    async def subtitle(
        shot_revision_id: str, lang: str, request: Request, ratio: Literal["16:9", "9:16"]
    ):
        record = await status(shot_revision_id, request, ratio)
        filename = record.get("subtitles", {}).get(lang)
        if record.get("status") != "succeeded" or not filename:
            raise HTTPException(404, "no current subtitle track for this ratio/language")
        path = Path(filename).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, "subtitle artifact missing")
        return {"content": path.read_text(encoding="utf-8"), "preview": True}


def _probe_seconds(video_path: Path) -> float:
    import subprocess

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=duration",
            "-of",
            "json",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return float(json.loads(result.stdout)["streams"][0]["duration"])


__all__ = ["install_assembly_endpoints"]
