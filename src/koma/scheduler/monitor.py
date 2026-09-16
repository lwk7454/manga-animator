"""Scheduler tick: submit, poll, download and advance accepted stage outputs.

The loop drains its last running job and holds known-offline work. Current JSON
persistence and artifact paths still need durable recovery and stale-result
fencing. See PLAN.md §§6-9; this is not a complete end-to-end run orchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..adapters import registry
from ..gpu.worker import ComfyUIWorker, GPUError
from ..log import get_logger
from ..models.domain import PanelState
from ..storage.series_store import SeriesStore
from .queue import is_paused, load_queue, next_pending, running_job, save_queue

log = get_logger("scheduler.monitor")


def monitor_once(
    data_dir: Path,
    config,
    store: SeriesStore,
    worker: ComfyUIWorker | None = None,
    slug: str | None = None,
) -> dict[str, Any]:
    """One scheduler pass over ALL series' queues (currently: the one series).

    Submits at most one new job if nothing is in flight and not paused.
    Reconciles in-flight jobs. Returns a summary for ``koma status``.
    """
    cfg_dir = config.resolved_config_dir()
    sdir = Path(data_dir)

    if worker is None:
        worker = ComfyUIWorker(
            host=str(config.get("gpu.host", "192.168.50.103")),
            port=int(config.get("gpu.port", 8188)),
        )

    summary: dict[str, Any] = {"submitted": 0, "downloaded": 0, "failed": 0, "paused":
        is_paused(sdir), "box_online": worker.is_online()}

    # A known-offline box holds work unchanged; absence cannot be established
    # by polling an unreachable server. A later tick can reconcile it.
    if not summary["box_online"]:
        return summary

    queue = load_queue(sdir)

    # 1. Reconcile in-flight jobs (works even while paused — drain semantics).
    running = running_job(queue)
    if running and running.get("prompt_id"):
        status = worker.poll(running["prompt_id"])
        if status.value == "generated":
            _finish_job(store, worker, running, slug)
            summary["downloaded"] += 1
        elif status.value == "failed":
            running["state"] = "failed"
            if running.get("kind") in ("keyframe", "render"):
                summary["failed"] += 1  # keyframe/render failures don't poison panels
            else:
                target = slug or running.get("slug")
                if target:
                    store.set_panel_state(target, running["panel_id"], PanelState.FAILED)
                summary["failed"] += 1

    # 2. Submit one new job if: not paused, box online, nothing in flight.
    if not summary["paused"] and summary["box_online"] and not running_job(queue):
        job = next_pending(queue)
        if job:
            # Only request a handoff for actual new work. This is not a lease:
            # concurrent vision/pixel commands still require shared ownership.
            from ..gpu.lanes import ensure_vision_lane

            try:
                lane = ensure_vision_lane(on=False, config=config)
                if lane["llama_running"]:
                    raise RuntimeError("vision service is still running")
            except Exception as exc:
                log.warning("vision lane handoff failed; holding pending job: %s", exc)
                save_queue(sdir, queue)
                return summary
            _submit_job(cfg_dir, worker, queue, job)
            if job.get("state") == "running":
                summary["submitted"] += 1
            else:
                summary["failed"] += 1

    save_queue(sdir, queue)
    return summary


def _submit_job(cfg_dir: Path, worker: ComfyUIWorker, queue: dict, job: dict) -> None:
    """Submit a queued job by kind (colorize | keyframe | render)."""
    if job.get("kind") == "keyframe":
        from ..steps.keyframe import _submit_keyframe_job

        _submit_keyframe_job(cfg_dir, worker, queue, job)
        return
    if job.get("kind") == "render":
        from ..steps.render import _submit_render_job

        _submit_render_job(cfg_dir, worker, queue, job)
        return

    try:
        resolved = registry.resolve_profile(cfg_dir, job["profile"])
    except Exception as exc:
        job["state"] = "failed"
        job["error"] = str(exc)
        log.error("job %s: profile load failed: %s", job["id"], exc)
        return

    import random
    import uuid

    from ..gpu.worker import _set_path  # single definition point

    wf = resolved["workflow"]
    inject = resolved["inject"]
    defaults = resolved["spec"].get("defaults", {})
    panel_name = worker.upload_image(Path(job["panel_png"]))
    sheet_name = worker.upload_image(Path(job["sheet_png"])) if job.get("sheet_png") else ""
    if not sheet_name:
        # no character sheet (palette-only colorize): feed the panel itself as
        # the ref so the node validates; ref-guidance collapses to self-color
        sheet_name = worker.upload_image(Path(job["panel_png"]))
    seed = random.randint(0, 2**31 - 1)

    _set_path(wf, inject["lineart"], panel_name)
    if sheet_name and "ref_image" in inject:
        _set_path(wf, inject["ref_image"], sheet_name)
    _set_path(wf, inject["seed"], seed)
    # Canvas policy per profile: Qwen-Edit colorize runs SQUARE 512x512
    # (verified live Sep 16 — wide native aspect produces garbage; the model
    # letterboxes internally via the EditPlus reference conditioning). Profiles
    # that declare long_side (legacy manganinja path) keep native aspect.
    import cv2

    _png = cv2.imread(str(job["panel_png"]))
    _h, _w = (_png.shape[0], _png.shape[1]) if _png is not None else (512, 512)
    if defaults.get("canvas") == "square_512" or "long_side" not in defaults:
        _rw = _rh = int(defaults.get("square", 512))
    else:
        _long = int(defaults.get("long_side", 512))
        if _w >= _h:
            _rw = _long
            _rh = max(16, round(_long * _h / _w / 16) * 16)
        else:
            _rh = _long
            _rw = max(16, round(_long * _w / _h / 16) * 16)
    _set_path(wf, inject["width"], _rw)
    _set_path(wf, inject["height"], _rh)
    _set_path(wf, inject["steps"], defaults.get("steps", 8))
    if "is_lineart" in inject:
        _set_path(wf, inject["is_lineart"], defaults.get("is_lineart", True))
    prefix = f"koma_{job['kind']}_{uuid.uuid4().hex[:8]}"
    _set_path(wf, inject["output"], prefix)

    try:
        pid = worker.submit(wf, prefix)
        job["prompt_id"] = pid
        job["state"] = "running"
        log.info("submitted %s -> %s", job["id"], pid)
    except GPUError as exc:
        job["state"] = "failed"
        job["error"] = str(exc)


def _finish_job(
    store: SeriesStore,
    worker: ComfyUIWorker,
    job: dict,
    slug: str | None = None,
) -> None:
    try:
        kind = job.get("kind") or ""
        out_key = {"keyframe": "output_png", "render": "output_mp4"}.get(kind, "output_png")
        worker.download_output_file(job["prompt_id"], Path(job[out_key]))
        job["state"] = "downloaded"
        target_slug = slug or job.get("slug")
        if target_slug is None:
            log.warning(
                "job %s finished but no series slug known — file kept, state not advanced",
                job["id"],
            )
        else:
            _advance_panel(store, target_slug, kind, job)
        # free VRAM after each heavy job (box is shared; vidgen doctrine)
        worker.free_vram()
    except GPUError as exc:
        job["state"] = "failed"
        job["error"] = str(exc)
        target = slug or job.get("slug")
        if target and (job.get("kind") or "") not in ("keyframe", "render"):
            store.set_panel_state(target, job["panel_id"], PanelState.FAILED)


def _advance_panel(store: SeriesStore, slug: str, kind: str, job: dict) -> None:
    """Advance the panel state for a finished job (lifecycle-safe, idempotent).

    Queued→COLORIZED etc. is legal; a panel already past the target state
    (crash-after-write re-run) is a no-op — never an error.
    """
    from ..models.lifecycle import can_transition

    _, panel = store.find_panel(slug, job["panel_id"])
    target = {
        "keyframe": PanelState.KEYFRAMED,
        "render": PanelState.GENERATED,
    }.get(kind, PanelState.COLORIZED)
    if panel.state == target:
        return
    if not can_transition(panel.state, target):
        # already beyond target (stale job finished late) — not an error
        log.info(
            "panel %s at %s; skip advancing to %s (stale completion)",
            job["panel_id"], panel.state.value, target.value,
        )
        return
    store.set_panel_state(slug, job["panel_id"], target)
