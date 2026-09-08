"""Render step — KEYFRAMED panels → i2v clips (PLAN Stage B3, Phase 5).

One job per (KEYFRAMED panel, ratio): keyframe + motion prompt → clip via the
selected video profile (stock `minimax_h3_fl2va` turbo today, fused
`minimax_h3_fused_turbo` as the A/B candidate — decision #027). Audio-first
sizing: clip length = audio bed duration x fps, CEIL, snapped to the node's
live 17n+5 grid (sizing.py; never pad or speed-adjust).

Doctrine (decision #001): appearance lives in the keyframe; the motion prompt
drives ONLY motion. Clips land as ``clip_<ratio>.mp4`` next to the keyframes;
state → QUEUED → GENERATING → GENERATED.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from ..adapters import registry
from ..log import get_logger
from ..models.domain import PanelState
from ..storage.series_store import SeriesStore

log = get_logger("steps.render")

# ratio → (width, height) submitted to the video node (0.35MP class, /16).
# H3 native canvas; the keyframe was already built at this ratio in B2.
RATIO_SIZES: dict[str, tuple[int, int]] = {
    "16:9": (768, 432),
    "9:16": (432, 768),
}

FPS = 24


def _config_dir(config) -> Path:
    return Path(config.resolved_config_dir())


def _video_profile(config, slug: str) -> str:
    """Series pin (decision #021) → config default → stock."""
    try:
        pinned = store_pin(config, slug)
        if pinned:
            return pinned
    except Exception:
        pass
    return str(config.get("video.profile_default", "minimax_h3_fl2va_turbo"))


def store_pin(config, slug: str) -> str | None:
    """Per-series video-profile pin (registry file; empty registry → None)."""
    pin_path = Path(config.resolved_data_dir()) / "series" / slug / "pins.json"
    if not pin_path.exists():
        return None
    data = json.loads(pin_path.read_text(encoding="utf-8"))
    return data.get("video_profile")


def queue_render(
    slug: str,
    store: SeriesStore,
    config,
    ratio: str = "16:9",
    chapter: int | None = None,
    profile_id: str | None = None,
) -> dict[str, Any]:
    """Queue one i2v job per KEYFRAMED panel (idempotent)."""
    from ..scheduler.queue import load_queue, save_queue

    if profile_id is None:
        profile_id = _video_profile(config, slug)
    cfg_dir = _config_dir(config)
    registry.resolve_profile(cfg_dir, profile_id)  # validates spec + workflow

    width, height = RATIO_SIZES.get(ratio, (768, 432))
    kf_w, kf_h = _keyframe_size_for(ratio)

    sdir = store.data_dir
    queue = load_queue(sdir)
    queued = skipped = 0

    keyframed = store.panels_in_state(slug, PanelState.KEYFRAMED)
    if chapter is not None:
        keyframed = [p for p in keyframed if p.panel_id.startswith(f"ch{chapter:02d}_")]

    for panel in keyframed:
        ch = int(panel.panel_id[2:4])
        pdir = store.chapter_dir(slug, ch) / "panels" / panel.panel_id
        kf = pdir / f"keyframe_{kf_w}x{kf_h}.png"
        if not kf.exists():
            log.warning("panel %s KEYFRAMED but %s missing — skip", panel.panel_id, kf.name)
            skipped += 1
            continue
        job_id = f"render_{panel.panel_id}_{ratio.replace(':', '')}"
        if any(j["id"] == job_id for j in queue["jobs"]):
            skipped += 1
            continue
        script = _script_entry(store, slug, ch, panel.panel_id)
        queue["jobs"].append({
            "id": job_id,
            "kind": "render",
            "panel_id": panel.panel_id,
            "chapter": ch,
            "ratio": ratio,
            "profile": profile_id,
            "keyframe_png": str(kf),
            "motion_prompt": script.get("motion_prompt", ""),
            "audio_duration_s": script.get("estimated_duration_s", 4.0),
            "width": width,
            "height": height,
            "output_mp4": str(pdir / f"clip_{ratio.replace(':', '')}.mp4"),
            "state": "pending",
            "prompt_id": None,
        })
        queued += 1

    save_queue(sdir, queue)
    return {"queued": queued, "skipped": skipped, "profile": profile_id, "ratio": ratio}


def _script_entry(store: SeriesStore, slug: str, ch: int, panel_id: str) -> dict[str, Any]:
    sp = store.chapter_dir(slug, ch) / "script.json"
    if not sp.exists():
        return {}
    return json.loads(sp.read_text(encoding="utf-8")).get("panels", {}).get(panel_id, {})


def _keyframe_size_for(ratio: str) -> tuple[int, int]:
    from ..steps.keyframe import RATIO_SIZES as KF_SIZES

    return KF_SIZES.get(ratio, (1024, 576))


def _submit_render_job(cfg_dir: Path, worker, queue: dict, job: dict) -> None:
    """Build + submit one i2v job: audio-first length, live frame grid."""
    import random

    resolved = registry.resolve_profile(cfg_dir, job["profile"])
    wf = resolved["workflow"]
    inject = resolved["inject"]
    defaults = resolved["spec"].get("defaults", {})

    kf_name = worker.upload_image(Path(job["keyframe_png"]))
    seed = random.randint(0, 2**31 - 1)

    # audio-first sizing on the LIVE grid (never hardcoded — vidgen #38)
    grid = worker.frame_grid(_frame_grid_node(inject, defaults))
    from ..gpu.sizing import clip_frames

    frames = clip_frames(float(job.get("audio_duration_s") or 4.0), FPS, grid)

    from ..gpu.worker import _set_path

    _set_path(wf, inject["keyframe"], kf_name)
    _set_path(wf, inject["motion"], job.get("motion_prompt") or defaults.get("motion", ""))
    _set_path(wf, inject["length"], frames)
    _set_path(wf, inject["fps"], FPS)
    _set_path(wf, inject["seed"], seed)
    if "width" in inject:
        _set_path(wf, inject["width"], job["width"])
    if "height" in inject:
        _set_path(wf, inject["height"], job["height"])
    if "steps" in inject:
        _set_path(wf, inject["steps"], int(defaults.get("steps", 4)))
    prefix = f"koma_render_{uuid.uuid4().hex[:8]}"
    _set_path(wf, inject["output"], prefix)

    try:
        pid = worker.submit(wf, prefix)
        job["prompt_id"] = pid
        job["state"] = "running"
        job["frames"] = frames
        log.info("submitted render job %s -> %s (%d frames)", job["id"], pid, frames)
    except Exception as exc:
        job["state"] = "failed"
        job["error"] = str(exc)
        log.error("render job %s submit failed: %s", job["id"], exc)


def _frame_grid_node(inject: dict[str, str], defaults: dict[str, Any]) -> str:
    """The node type the live frame grid is read from (profile override-able)."""
    return str(defaults.get("frame_grid_node", "MiniMaxH3ImageToVideo"))


def reconcile_renders(
    slug: str,
    store: SeriesStore,
    config,
    worker,
) -> dict[str, Any]:
    """Poll queued render jobs; download clips; advance panels. Idempotent."""
    from ..scheduler.queue import load_queue, save_queue

    sdir = store.data_dir
    queue = load_queue(sdir)
    downloaded = failed = 0

    for job in queue["jobs"]:
        if job.get("kind") != "render" or job.get("state") not in ("pending", "running"):
            continue
        pid = job.get("prompt_id")
        if not pid:
            continue
        status = worker.poll(pid)
        if status.value == "generating":
            job["state"] = "running"
            continue
        if status.value == "failed":
            job["state"] = "failed"
            failed += 1
            continue
        out = Path(job["output_mp4"])
        worker.download_output_file(pid, out)
        job["state"] = "downloaded"
        store.set_panel_state(slug, job["panel_id"], PanelState.GENERATED)
        downloaded += 1

    save_queue(sdir, queue)
    return {"downloaded": downloaded, "failed": failed}
