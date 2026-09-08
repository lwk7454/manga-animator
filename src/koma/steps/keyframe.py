"""Keyframe step — COLORIZED panels → per-ratio keyframes (PLAN Stage B2, Phase 4).

For each panel and each configured ratio, the colorized master is
outpainted/reframed to the target aspect via Qwen-Image-Edit-2509 (decision
#010/#011: reframe, don't crop rendered video; native JP text rendering keeps
balloons alive through outpainting). Output: ``keyframe_<WxH>.png`` next to
``color.png``; panel state → KEYFRAMED.

Doctrine (decision #001): the colorized master is the appearance authority —
the outpaint prompt is deliberately minimal ("extend this image, keep the
art style, characters and text unchanged"); the model is told to FILL, not
to reinvent. Lightning LoRA (8-step) keeps each panel to seconds.

GPU lane arbitration (#026): the keyframe step queues through the monitor,
which stops the vision lane before submitting.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from ..adapters import registry
from ..log import get_logger
from ..models.domain import PanelState
from ..storage.series_store import SeriesStore

log = get_logger("steps.keyframe")

# 16:9 and 9:16 masters at 0.4MP — Qwen-Edit sweet spot, snappable to /16.
RATIO_SIZES: dict[str, tuple[int, int]] = {
    "16:9": (1024, 576),
    "9:16": (576, 1024),
}


def _config_dir(config) -> Path:
    return Path(config.resolved_config_dir())


def queue_keyframes(
    slug: str,
    store: SeriesStore,
    config,
    ratios: list[str] | None = None,
    profile_id: str = "qwen_image_edit_2509_outpaint",
) -> dict[str, Any]:
    """Queue one outpaint job per (COLORIZED panel, ratio). Idempotent.

    Re-running skips panels whose keyframes already exist or are queued.
    """
    from ..scheduler.queue import load_queue, save_queue

    if ratios is None:
        ratios = ["16:9", "9:16"]
    cfg_dir = _config_dir(config)
    registry.resolve_profile(cfg_dir, profile_id)  # validates spec + workflow

    sdir = store.data_dir
    queue = load_queue(sdir)
    queued = skipped = 0

    colorized = store.panels_in_state(slug, PanelState.COLORIZED)
    for panel in colorized:
        ch = int(panel.panel_id[2:4])
        pdir = store.chapter_dir(slug, ch) / "panels" / panel.panel_id
        for ratio in ratios:
            width, height = RATIO_SIZES.get(ratio, (1024, 576))
            out = pdir / f"keyframe_{width}x{height}.png"
            job_id = f"kf_{panel.panel_id}_{width}x{height}"
            if out.exists() or any(j["id"] == job_id for j in queue["jobs"]):
                skipped += 1
                continue
            queue["jobs"].append({
                "id": job_id,
                "kind": "keyframe",
                "panel_id": panel.panel_id,
                "chapter": ch,
                "ratio": ratio,
                "profile": profile_id,
                "color_png": str(pdir / "color.png"),
                "output_png": str(out),
                "width": width,
                "height": height,
                "state": "pending",
                "prompt_id": None,
            })
            queued += 1

    save_queue(sdir, queue)
    return {
        "queued": queued,
        "skipped": skipped,
        "profile": profile_id,
        "ratios": ratios,
    }


def _submit_keyframe_job(cfg_dir: Path, worker, queue: dict, job: dict) -> None:
    """Build + submit one outpaint job (monitor callback).

    The color master is pre-sized to the EXACT target canvas (resize-to-fill:
    scale until both dimensions cover, then center-crop the overshoot) before
    upload — Qwen-Edit then regenerates the whole canvas at that ratio,
    lightning-LoRA keeps it faithful to the master (decision #001).
    """
    import random

    import cv2

    resolved = registry.resolve_profile(cfg_dir, job["profile"])
    wf = resolved["workflow"]
    inject = resolved["inject"]
    defaults = resolved["spec"].get("defaults", {})

    src = cv2.imread(str(job["color_png"]))
    if src is None:
        job["state"] = "failed"
        job["error"] = f"unreadable color master: {job['color_png']}"
        return
    tw, th = int(job["width"]), int(job["height"])
    sh, sw = src.shape[:2]
    scale = max(tw / sw, th / sh)
    resized = cv2.resize(src, (round(sw * scale), round(sh * scale)))
    h, w = resized.shape[:2]
    x0, y0 = (w - tw) // 2, (h - th) // 2
    canvas = resized[y0 : y0 + th, x0 : x0 + tw]
    ok, buf = cv2.imencode(".png", canvas)
    if not ok:
        job["state"] = "failed"
        job["error"] = "png encode failed"
        return
    import uuid as _uuid

    tmp = Path(__import__("tempfile").gettempdir()) / f"kf_{_uuid.uuid4().hex[:8]}.png"
    tmp.write_bytes(buf.tobytes())
    try:
        image_name = worker.upload_image(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    seed = random.randint(0, 2**31 - 1)

    from ..gpu.worker import _set_path

    _set_path(wf, inject["image"], image_name)
    _set_path(wf, inject["width"], job["width"])
    _set_path(wf, inject["height"], job["height"])
    _set_path(wf, inject["seed"], seed)
    prefix = f"koma_kf_{uuid.uuid4().hex[:8]}"
    _set_path(wf, inject["output"], prefix)
    # optional slots the profile may define (prompt, steps, guidance…)
    for slot in ("prompt", "steps", "cfg", "lora_strength"):
        if slot in inject:
            _set_path(wf, inject[slot], defaults.get(slot, 1.0 if slot == "lora_strength" else ""))

    try:
        pid = worker.submit(wf, prefix)
        job["prompt_id"] = pid
        job["state"] = "running"
        log.info("submitted keyframe job %s -> %s", job["id"], pid)
    except Exception as exc:  # GPUError or httpx — job-level isolation
        job["state"] = "failed"
        job["error"] = str(exc)
        log.error("keyframe job %s submit failed: %s", job["id"], exc)


def reconcile_keyframes(
    slug: str,
    store: SeriesStore,
    config,
    worker,
) -> dict[str, Any]:
    """Poll queued keyframe jobs; download finished ones; advance panels.

    A panel is KEYFRAMED when ALL ratio keyframes exist. Lost-in-crash jobs
    (nowhere on server) ⇒ FAILED (vidgen #32). Idempotent.
    """
    from ..scheduler.queue import load_queue, save_queue

    sdir = store.data_dir
    queue = load_queue(sdir)
    downloaded = failed = 0

    for job in queue["jobs"]:
        if job.get("kind") != "keyframe" or job.get("state") not in ("pending", "running"):
            continue
        pid = job.get("prompt_id")
        if not pid:
            continue  # monitor submits before reconciling
        status = worker.poll(pid)
        if status.value == "generating":
            job["state"] = "running"
            continue
        if status.value == "failed":
            job["state"] = "failed"
            failed += 1
            continue
        out = Path(job["output_png"])
        worker.download_output(pid, out)
        job["state"] = "downloaded"
        downloaded += 1
        # panel KEYFRAMED only when every ratio's keyframe is on disk
        pdir = out.parent
        ratios_done = all(
            (pdir / f"keyframe_{w}x{h}.png").exists()
            for w, h in RATIO_SIZES.values()
        )
        if ratios_done:
            store.set_panel_state(slug, job["panel_id"], PanelState.KEYFRAMED)

    save_queue(sdir, queue)
    return {"downloaded": downloaded, "failed": failed}
