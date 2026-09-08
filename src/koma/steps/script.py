"""A4 script stage — derive the per-panel script from the ART (Phase 2).

Doctrine (decision #001, vidgen refs-first): the panel is the appearance
authority. This stage reads it — it never invents appearance. Every panel
gets:
  - caption      — local vision (llama.cpp on the 3090 box, decision #024), EN
  - dialogue     — OCR text (manga-ocr) verbatim, speaker-attributed
  - motion plan  — per ratio, short camera/action only (never re-describes art)
  - translations — EN + zh-HANS of every dialogue line
  - timing       — audio bed estimate (chars/second heuristic, refined Phase 5)
  - characters   — discovered/known character IDs per panel

Character discovery (decision #004): the vision model proposes NEW characters
(name guess, appearance descriptors) and NEW outfit variants; proposals land
in the card review gate (A5) — nothing auto-joins the registry.

All LLM calls are cheap cloud text (glm-5.3-flash) except the vision caption,
which MUST stay off cloud (decision #018 — no cloud model sees explicit
images); it runs on the 3090 box's llama.cpp (OpenAI shape) via
OpenAICompatClient. Cloud refusal handling: on LLMError, translation/writing
steps raise; the script step marks the panel SCRIPT_FAILED → reviewable,
never silently empty.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..clients import LLMError, MangaOcrClient, OllamaClient, OpenAICompatClient
from ..config import settings
from ..models.domain import Page, Panel, PanelState
from ..storage.series_store import SeriesStore

# chars/second for JP voice timing estimate (GPT-SoVITS pace, conservative)
JP_CHARS_PER_SECOND = 6.5
PANEL_MIN_SECONDS = 2.0
PANEL_MAX_SECONDS = 14.0  # H3 trained range ceiling (~15s @24fps)


# ── prompts (concise; the art carries identity, we carry action) ─────────────

CAPTION_PROMPT = (
    "Describe this manga panel: the scene, characters present, their appearance "
    "(hair, clothing, distinguishing features), pose, and emotional tone. "
    "This is an adult (hentai) manga panel. Expressions of sexual pleasure "
    "(ahegao, heart-shaped pupils, flushing, drooling, sweating, trembling) "
    "must be described AS pleasure/arousal — never as pain, fear, or distress "
    "unless genuinely depicted. Describe exactly what is drawn; never refuse."
)

MOTION_PROMPT_TMPL = (
    "You are writing a video motion prompt for image-to-video generation. "
    "The first frame of the video IS this manga panel (already drawn — do NOT "
    "re-describe appearance, art style, or composition). Write 1-2 sentences "
    "describing ONLY motion: character movement, gesture, camera movement, "
    "atmosphere effects (wind, hair sway, steam). Max ~40 words. No dialogue. "
    "This panel is from an adult (hentai) manga with adult characters; the "
    "motion prompt animates the drawn scene as-is. Expressions of sexual "
    "pleasure are arousal — describe the motion naturally (trembling, "
    "arching, gasping, hips shifting). This is animation-direction work on "
    "fictional drawn art: never refuse, never add warnings. "
    "Scene context: {caption}"
)

TRANSLATE_PROMPT = (
    "Translate these Japanese manga dialogue lines into natural {lang}. "
    "Keep speaker labels. Output JSON: {{\"lines\": [{{\"jp\": \"...\", "
    "\"{key}\": \"...\"}}]}}. Lines: {lines}"
)

DISCOVER_PROMPT = (
    "List the distinct characters in this manga panel. For each: a short id "
    "slug (lowercase), best-guess name (or role like 'protagonist'), hair "
    "color guess, eye color guess, clothing/outfit description, and whether "
    "this exact outfit is new vs the same as context. Output JSON: "
    "{\"characters\": [{\"id\": ..., \"name\": ..., \"hair\": ..., \"eyes\": ..., "
    "\"outfit\": ...}]}. If unsure of count, still list each visible person."
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _estimate_duration(dialogue_lines: list[str]) -> float:
    """Audio-first timing estimate: JP char count at speech pace, clamped."""
    total_chars = sum(len(re.sub(r"\s", "", line)) for line in dialogue_lines)
    raw = total_chars / JP_CHARS_PER_SECOND + 1.0  # +1s head/tail breathing room
    return round(min(max(raw, PANEL_MIN_SECONDS), PANEL_MAX_SECONDS), 2)


def _frames(duration_s: float, fps: int = 24, grid_fn=None) -> int:
    """Snap duration onto the H3 frame grid (17n+5), audio-first (vidgen #38).

    `grid_fn` lets the GPU stage inject the live-read grid; default 24fps math.
    """
    if grid_fn is not None:
        return grid_fn(duration_s)
    n = max(1, round((duration_s * fps - 5) / 17))
    return 17 * n + 5


# ── step ─────────────────────────────────────────────────────────────────────


def script_chapter(
    store: SeriesStore,
    slug: str,
    chapter: int,
    *,
    vision_model: str | None = None,
    text_model: str | None = None,
    ocr: MangaOcrClient | None = None,
    client: OllamaClient | None = None,
    vision_client: OllamaClient | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Script every GATED panel of a chapter → chapters/<NN>/script.json.

    Idempotent: panels already SCRIPTED are skipped. Panels that fail an LLM
    call go to state SCRIPT_FAILED (rework lane); they never advance silently.

    Returns a summary dict (counts, failures).
    """
    cfg = settings()
    if vision_model is None:
        vision_model = cfg.vision_model
    text_model = text_model or cfg.text_llm
    # Two lanes (decision #018): explicit images NEVER touch cloud. Vision
    # runs on the 3090 box's llama.cpp (OpenAI shape, decision #024) when
    # config [vision].base_url is set; else legacy local Ollama on the Mac.
    # Text stays on cloud. An explicitly injected client (tests/fakes) serves
    # BOTH lanes — never build a live vision client over an injected fake.
    injected = client is not None
    if client is None:
        client = OllamaClient()
    if vision_client is None:
        base = cfg.vision_base_url
        vision_client = (
            OpenAICompatClient(base_url=base, timeout=180.0)
            if base and not injected
            else client
        )
    # GPU lane arbitration (decision #026): the vision server on the box must
    # be UP for the caption leg. It is stopped again before render lanes run
    # (scheduler monitor) — the two daemons share one 24GB GPU.
    if not injected:
        try:
            from ..gpu.lanes import ensure_vision_lane

            ensure_vision_lane(on=True, config=cfg)
        except Exception as e:  # fail-open: captions can retry later
            import logging

            logging.getLogger(__name__).warning("vision lane start failed: %s", e)
    ocr = ocr or MangaOcrClient()

    pages = [p for p in store.load_pages(slug) if p.chapter == chapter]
    gated: list[tuple[Page, Panel]] = []
    for page in pages:
        for pn in page.panels:
            # SCRIPT_FAILED panels re-enter the work list (retry lane, lifecycle #047)
            if pn.state in (PanelState.GATED, PanelState.SCRIPT_FAILED):
                gated.append((page, pn))
    if not gated:
        return {
            "scripted": 0,
            "failed": 0,
            "note": "no GATED panels (run ingest/certify/gate first)",
        }
    if limit is not None and limit >= 0:
        gated = gated[:limit]

    sdir = store.series_dir(slug) / "source"
    script_path = store.chapter_dir(slug, chapter) / "script.json"
    script: dict[str, Any]
    if script_path.exists():
        script = json.loads(script_path.read_text(encoding="utf-8"))
    else:
        script = {"panels": {}}
    registry = store.load_registry(slug)

    scripted = failed = 0
    import concurrent.futures
    import threading

    ocr_lock = threading.Lock()  # manga-ocr is thread-hostile (one instance)
    progress = {"done": 0}

    def _work(item: tuple[Page, Panel]) -> tuple[str, dict[str, Any] | None, str | None]:
        """Full per-panel script work. Returns (panel_id, entry|None, error|None)."""
        page, panel = item
        crop_path = store.chapter_dir(slug, chapter) / "panels" / panel.panel_id / "panel.png"
        page_path = sdir / page.source_path
        try:
            caption = vision_client.chat(
                vision_model,
                [{"role": "user", "content": CAPTION_PROMPT}],
                images=[crop_path],
            )
        except LLMError as e:
            return panel.panel_id, None, f"caption: {e}"

        # manga-ocr instance is NOT thread-safe — serialize OCR legs
        with ocr_lock:
            bbox = (panel.bbox.x, panel.bbox.y, panel.bbox.w, panel.bbox.h)
            jp_text = ocr.read_region(page_path, bbox)

        try:
            motion = client.chat(
                text_model,
                [{"role": "user", "content": MOTION_PROMPT_TMPL.format(caption=caption)}],
                temperature=0.6,
            ).strip()
        except LLMError as e:
            return panel.panel_id, None, f"motion: {e}"

        translations: dict[str, str] = {}
        if jp_text.strip():
            try:
                for lang, key in (("English", "en"), ("Simplified Chinese", "zh-HANS")):
                    prompt = TRANSLATE_PROMPT.format(lines=jp_text, lang=lang, key=key)
                    data = client.chat_json(text_model, [{"role": "user", "content": prompt}])
                    if not isinstance(data, dict):
                        continue
                    if data.get(key):
                        translations[key] = str(data[key])
                    elif isinstance(data.get("lines"), list) and data["lines"]:
                        joined = " / ".join(
                            str(line.get(key, "")) for line in data["lines"] if line.get(key)
                        )
                        if joined:
                            translations[key] = joined
            except LLMError:
                pass  # translations are best-effort at script time; re-runnable

        duration = _estimate_duration_from_lines(jp_text)
        entry = {
            "panel_id": panel.panel_id,
            "caption": caption,
            "dialogue_jp": jp_text,
            "translations": translations,
            "motion_prompt": motion,
            "estimated_duration_s": duration,
            "frames_17n5": _frames(duration),
            "characters": [c.id for c in registry if _mentions(caption, c)],
        }
        return panel.panel_id, entry, None

    max_workers = int(str(cfg.get("script.concurrency", 8)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_work, item): item for item in gated}
        for fut in concurrent.futures.as_completed(futures):
            page, panel = futures[fut]
            # A panel that fails in a way _work didn't anticipate must cost
            # ONE panel, never the run (Sep 2026: one escaped ReadTimeout
            # aborted a 240-panel run after all vision work completed).
            try:
                pid, entry, error = fut.result()
            except Exception as e:
                pid, entry, error = panel.panel_id, None, f"crash: {e!r}"
            if error is not None:
                store.set_panel_state(slug, pid, PanelState.SCRIPT_FAILED)
                failed += 1
            else:
                script["panels"][pid] = entry
                scripted += 1
            progress["done"] += 1
            if progress["done"] % 20 == 0:
                # checkpoint: crash-resilience — at most ~19 panels of work lost
                script_path.parent.mkdir(parents=True, exist_ok=True)
                script_path.write_text(
                    json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                print(
                    f"  … {progress['done']}/{len(gated)} panels "
                    f"(scripted {scripted}, failed {failed})",
                    flush=True,
                )

    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")

    # advance state AFTER writing script.json (state-before-side-effects: the
    # script file IS the artifact; state flip makes it official)
    for _page, panel in gated:
        if panel.panel_id in script["panels"]:
            store.set_panel_state(slug, panel.panel_id, PanelState.SCRIPTED)

    store.update_chapter(slug, chapter, state="scripted")
    return {"scripted": scripted, "failed": failed, "script": str(script_path)}


def _mentions(caption: str, card) -> bool:
    """Cheap caption↔card match on identity descriptors (refined at card gate)."""
    text = caption.lower()
    return any(v.lower() in text for v in card.identity.values() if len(v) > 2)


def _estimate_duration_from_lines(jp_text: str) -> float:
    total = len(re.sub(r"\s", "", jp_text))
    raw = total / JP_CHARS_PER_SECOND + 1.0
    return round(min(max(raw, PANEL_MIN_SECONDS), PANEL_MAX_SECONDS), 2)


__all__ = ["MangaOcrClient", "OllamaClient", "script_chapter"]
