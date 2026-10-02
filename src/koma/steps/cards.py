"""Local character proposals and canonical source candidate selection.

Discovery consolidates caption evidence and proposes cast/variant records.
Canonical candidates are paired with their actual images. Complete immutable
approvals, voice bindings and generated sheet review are pending in PLAN.md §14."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from ..clients import LLMError, OpenAICompatClient, text_client
from ..config import Settings
from ..log import get_logger
from ..models.domain import CharacterCard, CharacterVariant
from ..storage.series_store import SeriesStore

log = get_logger(__name__)

# Per-panel mention extraction (local text — captions already exist in
# script.json, no NEW vision calls needed for the consolidation pass).
MENTIONS_PROMPT = (
    "You are given vision captions of consecutive manga panels. Identify the "
    "RECURRING distinct characters (same person appearing across panels). "
    "For each recurring character output: id (short lowercase slug), "
    "best-guess name or role (e.g. 'heroine', 'teacher'), hair color+style, "
    "eye color, build, distinguishing features, list of panel_ids where they "
    "appear, and outfit descriptions with the panel_ids per outfit variant. "
    "One-off background characters are ignored. Output JSON: "
    '{{"characters": [{{"id": ..., "name": ..., "hair": ..., "eyes": ..., '
    '"build": ..., "features": ..., "panels": [...], '
    '"outfits": [{{"desc": ..., "panels": [...]}}]}}]}}.\n\n'
    "Panel captions:\n{captions}"
)

# Palette proposal (local text — text only, no images).
PALETTE_PROMPT = (
    "Given this character description from a manga, propose a color palette "
    "as JSON keys->hex (hair, eyes, skin, outfit_primary, outfit_secondary, "
    "lips, blush). Natural believable anime colors, no neon. Character: "
    "{identity}"
)

# Canonical-panel pick (VISION — local llama.cpp, LAN-only).
CANONICAL_PROMPT = (
    "These are candidate panels featuring the same manga character. Pick the "
    "ONE best canonical reference panel: front-facing or 3/4 view, face and "
    "upper body clearly visible, clean line art, no heavy motion blur, no "
    "extreme angle. Answer with just the panel id (like ch01_p015_02) and a "
    "one-line reason."
)


def _slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name.lower())
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "character"


def discover_cards(
    store: SeriesStore,
    slug: str,
    cfg: Settings,
    *,
    chapter: int | None = None,
    sample_max: int = 60,
) -> dict[str, Any]:
    """Consolidate script.json captions → registry card proposals (pending)."""
    chapters = (
        [chapter] if chapter is not None else [c.chapter_number for c in store.load_chapters(slug)]
    )
    # 1. gather caption evidence
    entries: list[tuple[str, str]] = []
    for ch in chapters:
        sp = store.chapter_dir(slug, ch) / "script.json"
        if not sp.exists():
            continue
        data = json.loads(sp.read_text(encoding="utf-8"))
        for pid, e in data.get("panels", {}).items():
            cap = (e.get("caption") or "").strip()
            if cap:
                entries.append((pid, cap))
    if not entries:
        return {"proposed": 0, "note": "no captions found (run koma script first)"}

    # 2. Local text consolidation into a cast proposal.
    client = _text_client(cfg)
    captions_block = "\n\n".join(f"{pid}: {cap}" for pid, cap in entries[:sample_max])
    prompt = MENTIONS_PROMPT.format(captions=captions_block)
    proposal = _chat_json_headroom(client, cfg.text_llm, prompt)
    if not isinstance(proposal, dict) or not proposal.get("characters"):
        return {"proposed": 0, "note": "discovery returned no characters"}

    # 3. build pending cards (never overwrite approved/rejected existing ones)
    existing = {c.id: c for c in store.load_registry(slug)}
    proposed = skipped = 0
    for raw in proposal["characters"]:
        cid = _slugify(str(raw.get("id") or raw.get("name") or "character"))
        if cid in existing and existing[cid].review_status != "pending":
            skipped += 1  # gate already ruled on this character
            continue
        identity = {
            k: str(v)
            for k, v in raw.items()
            if k in ("name", "hair", "eyes", "build", "features") and v
        }
        # palette proposal (cheap text call, best-effort)
        palette: dict[str, str] = {}
        try:
            pal = client.chat_json(
                cfg.text_llm,
                [{"role": "user", "content": PALETTE_PROMPT.format(identity=json.dumps(identity))}],
            )
            if isinstance(pal, dict):
                palette = {
                    k: str(v)
                    for k, v in pal.items()
                    if isinstance(v, str) and v.startswith("#")
                }
        except LLMError:
            pass
        # variants: one per distinct outfit desc
        variants: list[CharacterVariant] = []
        for i, outfit in enumerate(raw.get("outfits", [])[:4]):
            desc = str(outfit.get("desc") or outfit or "").strip()
            if not desc:
                continue
            panels = [str(p) for p in (outfit.get("panels") or [])]
            vid = _slugify(desc)[:24] or f"variant{i + 1}"
            variants.append(
                CharacterVariant(
                    id=vid,
                    version=1,
                    sheet="",  # filled after human approval (sheet render)
                    first_seen=panels[0] if panels else "unknown",
                    palette=palette,
                )
            )
        card = CharacterCard(
            id=cid,
            version=1,
            identity=identity,
            voice=None,
            variants=variants,
            review_status="pending",
        )
        store.upsert_card(slug, card)
        proposed += 1

    return {
        "proposed": proposed,
        "skipped": skipped,
        "registry": str(store._registry_path(slug)),
    }


def _text_client(cfg: Settings):
    return text_client(cfg, timeout=600.0)


def _chat_json_headroom(client, model: str, prompt: str) -> Any:
    """chat_json with bounded local-model response headroom.

    Retries once with a much larger num_predict when the reply dies at
    length (thinking leg starves the JSON content leg).
    """
    budget = 8000
    for _ in range(3):
        try:
            text = client.chat(
                model,
                [{"role": "user", "content": prompt}],
                json_mode=True,
                temperature=0.4,
                num_predict=budget,
            )
            break
        except LLMError as e:
            if "empty completion" not in str(e) and "starved" not in str(e):
                raise
            budget *= 4  # 8000 → 32000: thinking alone was measured at 22k
    else:
        raise LLMError("discovery: completion starved 3x (num_predict)")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise LLMError(f"discovery: no JSON in reply: {text[:200]}")
    return json.loads(match.group(0))


def pick_canonical_panels(
    store: SeriesStore,
    slug: str,
    cfg: Settings,
    *,
    per_card: int = 4,
) -> dict[str, dict[str, Any]]:
    """Vision-pick the canonical panel per pending card (LOCAL vision only).

    Candidates = panels whose caption mentions the card's identity
    descriptors. The vision model ranks them; the top pick becomes the
    sheet source once the human approves the card.
    """
    vision = _vision_client(cfg)
    registry = store.load_registry(slug)
    result: dict[str, dict[str, Any]] = {}
    for card in registry:
        if card.review_status != "pending":
            continue
        # identity VALUES are long sentences — match on significant WORDS
        # (>=4 chars, lowercased), not whole values.
        words = {
            w
            for v in card.identity.values()
            for w in re.findall(r"[a-z]{4,}", v.lower())
            if w not in STOPWORDS
        }
        candidates: list[tuple[str, int]] = []
        for ch in [c.chapter_number for c in store.load_chapters(slug)]:
            sp = store.chapter_dir(slug, ch) / "script.json"
            if not sp.exists():
                continue
            data = json.loads(sp.read_text(encoding="utf-8"))
            for pid, e in data.get("panels", {}).items():
                cap = (e.get("caption") or "").lower()
                score = sum(1 for w in words if w in cap)
                if score >= max(2, len(words) // 6):
                    candidates.append((pid, score))
        if not candidates:
            result[card.id] = {"canonical": None, "note": "no caption matches"}
            continue
        candidates.sort(key=lambda t: -t[1])
        top = [pid for pid, _ in candidates[:per_card]]
        candidates_with_paths = [
            (pid, store.panel_image_path(slug, int(pid.split("_")[0][2:]), pid))
            for pid in top
        ]
        available = [(pid, path) for pid, path in candidates_with_paths if path.is_file()]
        top = [pid for pid, _ in available]
        images = [path for _, path in available]
        if not images:
            result[card.id] = {"canonical": None, "note": "panel pngs missing"}
            continue
        numbered = "\n".join(f"{i + 1}. {pid}" for i, pid in enumerate(top))
        try:
            answer = vision.chat(
                cfg.vision_model,
                [{"role": "user", "content": f"{CANONICAL_PROMPT}\n\n{numbered}"}],
                images=images,
            )
        except LLMError as e:
            result[card.id] = {"canonical": None, "note": f"vision error: {e}"}
            continue
        m = re.search(r"ch\d+_p\d+_\d+", answer)
        result[card.id] = {
            "canonical": m.group(0) if m and m.group(0) in top else None,
            "candidates": top,
            "reason": answer.strip()[:200],
        }
    return result


STOPWORDS = frozenset(
    "this that with from have has their them they than then there these those "
    "into onto over under about above below after before during while where "
    "which what whom whose when your yours herself himself itself themselves "
    "wearing wear worn looking looks appear appears appeared visible showing "
    "panel manga character girl woman male female person possibly nickname "
    "light dark colored exact shade unclear grayscale reading reads".split()
)


def _vision_client(cfg: Settings):
    base = cfg.vision_base_url
    if not base:
        raise LLMError("no vision base_url configured")
    return OpenAICompatClient(base_url=base, timeout=180.0)


__all__ = ["discover_cards", "pick_canonical_panels"]
