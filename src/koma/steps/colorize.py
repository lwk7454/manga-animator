"""Queue colorization for SCRIPTED panels, or copy native-color inputs.

Approved variant-sheet binding and durable artifact identity remain incomplete.
The monitor applies source-aspect sizing and basic service handoff. Production
contracts are in PLAN.md §§14-16; current jobs are still file-backed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..adapters import registry
from ..log import get_logger
from ..models.domain import PanelState
from ..storage.series_store import SeriesStore

log = get_logger("steps.colorize")


def _config_dir(config) -> Path:
    return config.resolved_config_dir()


def queue_colorize(
    slug: str,
    store: SeriesStore,
    config,
    chapter: int | None = None,
) -> dict[str, Any]:
    """Queue colorize jobs for every SCRIPTED panel of a B&W series (idempotent).

    Reads the profile spec (manganinja_sd15), resolves panels + sheet paths,
    writes job records into the chapter queue (state/queue.json via scheduler),
    and flips panels to QUEUED. Re-running skips already-queued panels.
    """
    from ..scheduler.queue import load_queue, save_queue

    sdir = store.data_dir
    profile_id = "qwen_image_edit_2509_colorize"
    registry.resolve_profile(_config_dir(config), profile_id)  # validates spec exists

    series = store.load_series(slug)
    if series.meta.is_color_source:
        # Color sources skip colorization entirely: panels seed the sheets
        #. Flip SCRIPTED panels straight to COLORIZED with
        # color.png = panel.png (documented skip-forward edge).
        skipped_panels = []
        for p in store.panels_in_state(slug, PanelState.SCRIPTED):
            ch = int(p.panel_id.split("_")[0][2:])
            if chapter is not None and ch != chapter:
                continue
            color = store.chapter_dir(slug, ch) / "panels" / p.panel_id / "color.png"
            panel_png = store.chapter_dir(slug, ch) / "panels" / p.panel_id / "panel.png"
            color.parent.mkdir(parents=True, exist_ok=True)
            color.write_bytes(panel_png.read_bytes())
            store.set_panel_state(slug, p.panel_id, PanelState.COLORIZED)
            skipped_panels.append(p.panel_id)
        return {"queued": 0, "skipped": len(skipped_panels), "profile": profile_id,
                "note": "color source — color.png = panel.png", "skipped_panels": skipped_panels}

    chapters = [chapter] if chapter else sorted({
        int(p.panel_id.split("_")[0][2:])
        for p in store.panels_in_state(slug, PanelState.SCRIPTED)
    })
    queue = load_queue(sdir)
    queued = 0
    skipped = 0

    for ch in chapters:
        all_scripted = store.panels_in_state(slug, PanelState.SCRIPTED)
        chapter_panels = [p for p in all_scripted if p.panel_id.startswith(f"ch{ch:02d}_")]
        if not chapter_panels:
            continue
        script_path = store.chapter_dir(slug, ch) / "script.json"
        script = (
            json.loads(script_path.read_text(encoding="utf-8"))
            if script_path.exists()
            else {"panels": {}}
        )
        registry_cards = store.load_registry(slug)

        for panel in chapter_panels:
            entry = script.get("panels", {}).get(panel.panel_id)
            if not entry:
                log.warning("panel %s scripted but missing from script.json — skip", panel.panel_id)
                continue
            # choose the sheet: first mentioned character's card, else none (palette-only)
            sheet_path = _sheet_for(entry, registry_cards, slug, store)
            job_id = f"color_{panel.panel_id}"
            if any(j["id"] == job_id for j in queue["jobs"]):
                skipped += 1
                continue
            queue["jobs"].append({
                "id": job_id,
                "kind": "colorize",
                "panel_id": panel.panel_id,
                "chapter": ch,
                "profile": profile_id,
                "panel_png": str(store.chapter_dir(slug, ch) / "panels" / panel.panel_id /
                    "panel.png"),
                "sheet_png": str(sheet_path) if sheet_path else "",
                "output_png": str(
                    store.chapter_dir(slug, ch) / "panels" / panel.panel_id / "color.png"
                ),
                "state": "pending",
                "prompt_id": None,
            })
            # NOTE: panels stay SCRIPTED until the job's output is downloaded
            #. QUEUED/GENERATING/GENERATED are RENDER-lane
            # states — using them here made download-time advance a backward
            # (illegal) transition and stranded 243 panels at `queued`.
            queued += 1

    save_queue(sdir, queue)
    return {"queued": queued, "skipped": skipped, "profile": profile_id}


def _sheet_for(
    entry: dict[str, Any],
    cards: list,
    slug: str,
    store: SeriesStore,
) -> Path | None:
    """First mentioned character's approved sheet, else None (auto color)."""
    for card in cards:
        if card.id in entry.get("characters", []):
            sheet = getattr(card, "sheet_path", None) or getattr(card, "sheet", None)
            if sheet:
                p = Path(sheet)
                if not p.is_absolute():
                    p = store.series_dir(slug) / p
                if p.exists():
                    return p
    return None


def reconcile_colorize(
    slug: str,
    store: SeriesStore,
    config,
    worker: Any,
) -> dict[str, Any]:
    """Poll queued colorize jobs; download finished ones; advance panels.

    Lost-in-crash jobs (nowhere on server) ⇒ FAILED (vidgen #32). Idempotent:
    finished panels are skipped on re-run.
    """
    from ..scheduler.queue import load_queue, save_queue

    sdir = store.data_dir
    queue = load_queue(sdir)
    profile_id = "qwen_image_edit_2509_colorize"
    registry.resolve_profile(_config_dir(config), profile_id)  # validates spec exists

    downloaded = failed = 0
    for job in queue["jobs"]:
        if job.get("kind") != "colorize" or job.get("state") not in ("pending", "running"):
            continue
        pid = job.get("prompt_id")
        if not pid:
            continue  # not yet submitted (monitor submits before reconciling)
        status = worker.poll(pid)
        if status.value == "generating":
            job["state"] = "running"
            continue
        if status.value == "failed":
            job["state"] = "failed"
            store.set_panel_state(slug, job["panel_id"], PanelState.FAILED)
            failed += 1
            continue
        out = Path(job["output_png"])
        worker.download_output(pid, out)
        job["state"] = "downloaded"
        store.set_panel_state(slug, job["panel_id"], PanelState.COLORIZED)
        downloaded += 1

    save_queue(sdir, queue)
    return {"downloaded": downloaded, "failed": failed}
