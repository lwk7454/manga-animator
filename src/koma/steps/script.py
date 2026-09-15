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

All model requests stay on local endpoints. Missing required translations
leave a panel incomplete and retry only missing substeps with unchanged inputs.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..clients import LLMError, MangaOcrClient, OllamaClient, OpenAICompatClient
from ..config import settings
from ..models.domain import PanelState
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
    "Translate these {source_language} manga dialogue lines into natural {lang}. "
    'Keep speaker labels. Output JSON: {{"lines": [{{"jp": "...", '
    '"{key}": "..."}}]}}. Lines: {lines}'
)

DISCOVER_PROMPT = (
    "List the distinct characters in this manga panel. For each: a short id "
    "slug (lowercase), best-guess name (or role like 'protagonist'), hair "
    "color guess, eye color guess, clothing/outfit description, and whether "
    "this exact outfit is new vs the same as context. Output JSON: "
    '{"characters": [{"id": ..., "name": ..., "hair": ..., "eyes": ..., '
    '"outfit": ...}]}. If unsure of count, still list each visible person.'
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
    from ..gpu.sizing import MINIMAX_H3_GRID, clip_frames

    return clip_frames(duration_s, fps, MINIMAX_H3_GRID)


# ── step ─────────────────────────────────────────────────────────────────────


def _entry_complete(entry: dict, languages: list[str]) -> bool:
    if not entry.get("input_revision"):
        return False
    if not all(
        isinstance(entry.get(k), str) and entry[k].strip() for k in ("caption", "motion_prompt")
    ):
        return False
    dialogue = entry.get("dialogue_jp")
    if not isinstance(dialogue, str):
        return False
    if not dialogue.strip():
        return True
    translations = entry.get("translations", {})
    return all(
        isinstance(translations.get(key), str) and translations[key].strip() for key in languages
    )


def _input_revision(
    store, slug, chapter, page, panel, vision_model, text_model, source_language
) -> str:
    import hashlib

    pdir = store.chapter_dir(slug, chapter) / "panels" / panel.panel_id
    source = store.series_dir(slug) / "source" / page.source_path
    digest = hashlib.sha256()
    for path in (source, pdir / "panel.png"):
        with path.open("rb") as fh:
            digest.update(hashlib.file_digest(fh, "sha256").digest())
    digest.update(
        json.dumps(
            [
                panel.bbox.model_dump(),
                vision_model,
                text_model,
                source_language,
                CAPTION_PROMPT,
                MOTION_PROMPT_TMPL,
                TRANSLATE_PROMPT,
            ],
            sort_keys=True,
        ).encode()
    )
    return digest.hexdigest()


def _translation(data: Any, key: str, source: str) -> str:
    """Reject missing/blank rows instead of accepting a partial translation."""
    if not isinstance(data, dict):
        raise LLMError(f"translation {key}: expected an object")
    lines = data.get("lines")
    if isinstance(lines, list):
        expected = len([line for line in source.splitlines() if line.strip()])
        if len(lines) != expected or not lines:
            raise LLMError(f"translation {key}: incomplete line count")
        if not all(
            isinstance(line, dict) and isinstance(line.get(key), str) and line[key].strip()
            for line in lines
        ):
            raise LLMError(f"translation {key}: missing or blank line")
        return " / ".join(line[key].strip() for line in lines)
    # A single-line aggregate response is supported by older local adapters.
    if len(source.splitlines()) == 1 and isinstance(data.get(key), str) and data[key].strip():
        return data[key].strip()
    raise LLMError(f"translation {key}: missing required text")


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
    """Script reviewed panels locally, retaining valid substeps for retry.

    Required translations block completion. Entries are atomically checkpointed
    before panel advancement. One command must own this store at a time; a
    cross-process command lease is separate work.
    """
    import concurrent.futures
    import copy
    import threading

    from ..clients import text_client
    from ..storage.series_store import _atomic_write_json

    cfg = settings()
    vision_model = vision_model or cfg.vision_model
    text_model = text_model or cfg.text_llm
    meta = store.load_series(slug).meta
    languages = list(dict.fromkeys(meta.subtitle_languages))
    pages = [page for page in store.load_pages(slug) if page.chapter == chapter]
    if not pages:
        raise ValueError(f"chapter {chapter} has no imported pages")
    script_path = store.chapter_dir(slug, chapter) / "script.json"
    script = json.loads(script_path.read_text()) if script_path.exists() else {"panels": {}}
    eligible = {PanelState.ORDERED, PanelState.GATED, PanelState.SCRIPT_FAILED, PanelState.SCRIPTED}
    work = []
    for page in pages:
        for panel in page.panels:
            if panel.state not in eligible:
                continue
            previous = script["panels"].get(panel.panel_id, {})
            try:
                revision = _input_revision(
                    store, slug, chapter, page, panel, vision_model, text_model, meta.language
                )
            except OSError:
                revision = None  # worker records a failure; never reuses stale data
            if (
                panel.state is PanelState.SCRIPTED
                and revision
                and previous.get("input_revision") == revision
                and _entry_complete(previous, languages)
            ):
                continue
            work.append((page, panel, revision))
    remaining = {panel.panel_id for _, panel, _ in work}

    def update_completion():
        complete = not remaining and all(
            panel.state not in (PanelState.DETECTED, PanelState.AWAITING_ORDER_REVIEW)
            and _entry_complete(script["panels"].get(panel.panel_id, {}), languages)
            for page in pages
            for panel in page.panels
        )
        current = next(ch for ch in store.load_chapters(slug) if ch.chapter_number == chapter)
        if complete and current.state in ("rendering", "complete"):
            return
        store.update_chapter(slug, chapter, state="scripted" if complete else "script_incomplete")

    if limit is not None:
        if limit < 0:
            raise ValueError("limit must be nonnegative")
        work = work[:limit]
    if dry_run:
        return {"scripted": 0, "failed": 0, "would_script": len(work)}
    if not work:
        update_completion()
        return {"scripted": 0, "failed": 0, "note": "no selected incomplete reviewed panels"}

    injected = client is not None
    owned = []
    try:
        if client is None:
            client = text_client(cfg)
            owned.append(client)
        if vision_client is None:
            if injected:
                vision_client = client
            else:
                vision_client = OpenAICompatClient(
                    base_url=cfg.vision_base_url or "http://127.0.0.1:11434", timeout=180.0
                )
                owned.append(vision_client)
        if not injected:
            from ..gpu.lanes import ensure_vision_lane

            lane = ensure_vision_lane(on=True, config=cfg)
            if not lane["llama_running"]:
                raise LLMError("local inference service did not start")
        ocr = ocr or MangaOcrClient()
        ocr_lock = threading.Lock()
        registry = store.load_registry(slug)

        def _work(item):
            page, panel, revision = item
            entry = {"panel_id": panel.panel_id, "translations": {}}
            if (
                revision
                and script["panels"].get(panel.panel_id, {}).get("input_revision") == revision
            ):
                entry = copy.deepcopy(script["panels"][panel.panel_id])
            entry["complete"] = False
            try:
                if revision is None:
                    raise LLMError("source page or panel crop is missing/unreadable")
                entry["input_revision"] = revision
                crop = store.chapter_dir(slug, chapter) / "panels" / panel.panel_id / "panel.png"
                if not entry.get("caption"):
                    caption = vision_client.chat(
                        vision_model, [{"role": "user", "content": CAPTION_PROMPT}], images=[crop]
                    )
                    if not isinstance(caption, str) or not caption.strip():
                        raise LLMError("empty caption")
                    entry["caption"] = caption.strip()
                if "dialogue_jp" not in entry:
                    source = store.series_dir(slug) / "source" / page.source_path
                    with ocr_lock:
                        dialogue = ocr.read_region(
                            source, (panel.bbox.x, panel.bbox.y, panel.bbox.w, panel.bbox.h)
                        )
                    if not isinstance(dialogue, str):
                        raise LLMError("OCR did not return text")
                    entry["dialogue_jp"] = dialogue
                if not entry.get("motion_prompt"):
                    motion = client.chat(
                        text_model,
                        [
                            {
                                "role": "user",
                                "content": MOTION_PROMPT_TMPL.format(caption=entry["caption"]),
                            }
                        ],
                        temperature=0.6,
                    )
                    if not isinstance(motion, str) or not motion.strip():
                        raise LLMError("empty motion prompt")
                    entry["motion_prompt"] = motion.strip()
                translations = entry.setdefault("translations", {})
                errors = []
                if entry["dialogue_jp"].strip():
                    for key in languages:
                        if isinstance(translations.get(key), str) and translations[key].strip():
                            continue
                        language = {"en": "English", "zh-HANS": "Simplified Chinese"}.get(key, key)
                        prompt = TRANSLATE_PROMPT.format(
                            lines=entry["dialogue_jp"],
                            lang=language,
                            key=key,
                            source_language=meta.language,
                        )
                        try:
                            data = client.chat_json(
                                text_model, [{"role": "user", "content": prompt}]
                            )
                            translations[key] = _translation(data, key, entry["dialogue_jp"])
                        except Exception as exc:
                            errors.append(f"{key}: {exc}")
                if errors:
                    raise LLMError("; ".join(errors))
                duration = _estimate_duration_from_lines(entry["dialogue_jp"])
                entry.update(
                    estimated_duration_s=duration,
                    frames_17n5=_frames(duration),
                    characters=[card.id for card in registry if _mentions(entry["caption"], card)],
                )
                entry["complete"] = _entry_complete(entry, languages)
                if not entry["complete"]:
                    raise LLMError("incomplete script entry")
                entry.pop("error", None)
                return panel.panel_id, entry, None
            except Exception as exc:
                entry["error"] = str(exc)
                return panel.panel_id, entry, str(exc)

        scripted = failed = 0
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, int(cfg.get("script.concurrency", 1)))
        ) as pool:
            futures = {pool.submit(_work, item): item for item in work}
            for future in concurrent.futures.as_completed(futures):
                pid, entry, error = future.result()
                script["panels"][pid] = entry
                _atomic_write_json(script_path, script)
                target = PanelState.SCRIPT_FAILED if error else PanelState.SCRIPTED
                store.set_panel_state(slug, pid, target)
                failed += int(error is not None)
                scripted += int(error is None)
                if error is None:
                    remaining.discard(pid)
        update_completion()
        return {"scripted": scripted, "failed": failed, "script": str(script_path)}
    finally:
        for transport in owned:
            transport.close()


def _mentions(caption: str, card) -> bool:
    """Cheap caption↔card match on identity descriptors (refined at card gate)."""
    text = caption.lower()
    return any(v.lower() in text for v in card.identity.values() if len(v) > 2)


def _estimate_duration_from_lines(jp_text: str) -> float:
    total = len(re.sub(r"\s", "", jp_text))
    raw = total / JP_CHARS_PER_SECOND + 1.0
    return round(min(max(raw, PANEL_MIN_SECONDS), PANEL_MAX_SECONDS), 2)


__all__ = ["MangaOcrClient", "OllamaClient", "script_chapter"]
