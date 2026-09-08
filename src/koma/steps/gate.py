"""A3 age-gate — mandatory, fail-closed, OR-reject (decision #013, PLAN §3).

Ensemble (any single signal rejects — bias to recall):
  1. Civitai SigLIP2 anime age classifier — child-class probability vs
     tuned-down threshold (default ships 0.622; Civitai's own calibration
     misses ~16% of anime minors at default → we default LOWER).
  2. WD tagger v3 (wd-swinv2) — literal loli/shota/child tags ≥ threshold.
  3. (optional) Llama-Guard-4 via OpenRouter — S1 flag rejects; disabled
     unless KOMA_OPENROUTER_API_KEY is set (config gate.llama_guard = true).

Fail-closed contract (non-negotiable):
  - ANY model error, timeout, or missing score ⇒ the page/chapter is
    REJECTED with reason — a run that cannot verify safety is a rejected run.
  - Gate runs BEFORE any generation step and per page AND per chapter.
  - Every decision is logged (audit trail) to data/…/gate_log.jsonl.

The gate itself is content-agnostic and never sees or needs the LLM.
Red-team fixtures: tests seed images from the gate's own test suite (synthetic
"child-coded" fixtures are tagged by the WD-tagger path; unit tests assert
fail-closed behavior without downloading real model weights by injecting a
fake scorer — the live ensemble is validated by `koma gate --verify` on the
real models at first use).
"""

from __future__ import annotations

import enum
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models.domain import Chapter, Page, PanelState

# ── thresholds (bias to recall — decision #013) ──────────────────────────────
CIVITAI_CHILD_THRESHOLD = 0.30  # shipped default 0.622 misses ~16% — tuned DOWN
WD_MINOR_TAG_THRESHOLD = 0.35  # loli/shota/child tag confidence floor
GATE_TIMEOUT_S = 120.0  # per model, per page


class GateVerdict(enum.StrEnum):
    PASS = "pass"
    REJECT = "reject"


class GateSignal(enum.StrEnum):
    CIVITAI_CHILD = "civitai_child"
    WD_MINOR_TAG = "wd_minor_tag"
    LLAMA_GUARD_S1 = "llama_guard_s1"
    MODEL_ERROR = "model_error"  # fail-closed marker


WD_MINOR_TAGS = ("loli", "shota", "child")


@dataclass
class GateFinding:
    signal: GateSignal
    triggered: bool
    detail: str


@dataclass
class GateResult:
    verdict: GateVerdict
    findings: list[GateFinding] = field(default_factory=list)
    model_errors: list[str] = field(default_factory=list)

    @property
    def reject_reasons(self) -> list[str]:
        return [f.detail for f in self.findings if f.triggered]


class GateModelError(Exception):
    """Raised by any gate scorer that cannot produce a verdict — fail closed."""


# ── scorers (injected; live implementations load weights lazily) ─────────────


_CIVITAI_CACHE: dict[str, Any] = {}


def _load_civitai():
    """Lazy-load SigLIP2 age classifier (~0.9GB, cached in HF cache).

    The Civitai repo ships a SiglipConfig (single-label classification over 5
    age buckets: Child 0-12 / Teenager / Adult / Middle Age / Aged). We build
    via SiglipForImageClassification + SiglipImageProcessor directly (the
    AutoTokenizer path fails on this repo in transformers 5.x) and restore
    id2label from the repo's config.json. score_civitai treats BOTH the Child
    AND Teenager buckets as minor signals (bias to recall — decision #013).
    """
    if "model" not in _CIVITAI_CACHE:
        import json as _json

        from huggingface_hub import hf_hub_download
        from transformers import (
            SiglipForImageClassification,
            SiglipImageProcessor,
        )

        mid = "Civitai/Age-Classification-SigLIP2-anime-finetune"
        cfg_path = hf_hub_download(mid, "config.json")
        with open(cfg_path, encoding="utf-8") as f:
            hf_config = _json.load(f)
        processor = SiglipImageProcessor.from_pretrained(mid)
        model = SiglipForImageClassification.from_pretrained(mid)
        if "id2label" in hf_config:
            model.config.id2label = {int(k): str(v) for k, v in hf_config["id2label"].items()}
        _CIVITAI_CACHE["processor"] = processor
        _CIVITAI_CACHE["model"] = model
    return _CIVITAI_CACHE["model"], _CIVITAI_CACHE["processor"]


def score_civitai(image_path: Path) -> dict[str, float]:
    """Civitai SigLIP2 anime age classifier → {'child': p, 'raw': {...}}."""
    import torch
    from PIL import Image

    model, processor = _load_civitai()
    img = Image.open(image_path).convert("RGB")
    inputs = processor(images=img, return_tensors="pt")
    with torch.no_grad():
        logits = model(**inputs).logits[0]
    probs = torch.softmax(logits, dim=-1).tolist()
    id2label = getattr(model.config, "id2label", {}) or {}
    labels = [str(id2label.get(i, f"label_{i}")) for i in range(len(probs))]
    pairs = dict(zip(labels, probs, strict=True))
    child = max(
        (
            v
            for k, v in pairs.items()
            if "child" in k.lower() or "teen" in k.lower() or "minor" in k.lower()
        ),
        default=0.0,
    )
    return {"child": float(child), "raw": pairs}  # type: ignore[dict-item]


_WD_CACHE: dict[str, Any] = {}


def _load_wd():
    """Lazy-load WD tagger ONNX session + tag table (~400MB, cached)."""
    import csv
    import io

    import onnxruntime as ort
    from huggingface_hub import hf_hub_download

    if "session" not in _WD_CACHE:
        repo = "SmilingWolf/wd-swinv2-tagger-v3"
        model_path = hf_hub_download(repo, "model.onnx")
        csv_path = hf_hub_download(repo, "selected_tags.csv")
        session = ort.InferenceSession(model_path)
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(io.StringIO(f.read()))
            # NOTE: tag_id is the danbooru post count, NOT a vector index —
            # the output index is simply the row position (verified live).
            tags = [(i, row["name"]) for i, row in enumerate(reader)]
        _WD_CACHE["session"] = session
        _WD_CACHE["tags"] = tags
    return _WD_CACHE["session"], _WD_CACHE["tags"]


def score_wd_tagger(image_path: Path) -> dict[str, float]:
    """WD tagger v3 (wd-swinv2, ONNX) → {tag: confidence}."""
    import numpy as np
    from PIL import Image

    session, tags = _load_wd()

    img = Image.open(image_path).convert("RGBA").resize((448, 448))
    arr = np.asarray(img, dtype=np.float32)
    arr[..., 3] = 255.0  # flatten alpha
    x = np.expand_dims(arr[..., :3][..., ::-1], 0)  # RGB→BGR, batch 1
    input_name = session.get_inputs()[0].name
    out = session.run(None, {input_name: x})[0][0]
    if len(out) < len(tags):  # defensive: tag table larger than output vector
        tags = tags[: len(out)]
    return {name: float(out[idx]) for idx, name in tags}


def score_llama_guard(image_path: Path, prompt: str = "manga page") -> str | None:
    """Optional Llama-Guard-4 via OpenRouter → 'S1' flag or None. Needs key."""
    import os

    import httpx

    api_key = os.environ.get("KOMA_OPENROUTER_API_KEY")
    if not api_key:
        return None  # disabled — not an error (it's an optional third signal)
    resp = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": "meta-llama/llama-guard-4-12b",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": _data_url(image_path)},
                        },
                    ],
                }
            ],
        },
        timeout=GATE_TIMEOUT_S,
    )
    resp.raise_for_status()
    out = resp.json()["choices"][0]["message"]["content"]
    return out if "S1" in out else None


def _data_url(path: Path) -> str:
    import base64

    b64 = base64.b64encode(Path(path).read_bytes()).decode()
    return f"data:image/png;base64,{b64}"


# ── the gate ─────────────────────────────────────────────────────────────────


def gate_page(
    image_path: Path,
    *,
    civitai_threshold: float = CIVITAI_CHILD_THRESHOLD,
    wd_threshold: float = WD_MINOR_TAG_THRESHOLD,
    use_llama_guard: bool = False,
    civitai_scorer=score_civitai,  # injectable for tests
    wd_scorer=score_wd_tagger,  # injectable for tests
) -> GateResult:
    """Gate ONE image. OR-reject, fail-closed. Never raises — errors reject."""
    findings: list[GateFinding] = []
    errors: list[str] = []

    # Signal 1 — Civitai SigLIP2
    try:
        start = time.monotonic()
        scores = civitai_scorer(image_path)
        if time.monotonic() - start > GATE_TIMEOUT_S:
            raise GateModelError("civitai timeout")
        p_child = float(scores.get("child", 0.0))
        findings.append(
            GateFinding(
                GateSignal.CIVITAI_CHILD,
                triggered=p_child >= civitai_threshold,
                detail=f"civitai child p={p_child:.3f} (threshold {civitai_threshold})",
            )
        )
    except Exception as e:
        findings.append(
            GateFinding(GateSignal.MODEL_ERROR, triggered=True, detail=f"civitai error: {e}")
        )
        errors.append(f"civitai: {e}")

    # Signal 2 — WD tagger minor tags
    try:
        start = time.monotonic()
        tags = wd_scorer(image_path)
        if time.monotonic() - start > GATE_TIMEOUT_S:
            raise GateModelError("wd timeout")
        hits = {t: tags[t] for t in WD_MINOR_TAGS if t in tags and tags[t] >= wd_threshold}
        findings.append(
            GateFinding(
                GateSignal.WD_MINOR_TAG,
                triggered=bool(hits),
                detail=f"wd minor tags: {hits}" if hits else "wd minor tags clean",
            )
        )
    except Exception as e:
        detail = f"wd error: {e}"
        findings.append(GateFinding(GateSignal.MODEL_ERROR, triggered=True, detail=detail))
        errors.append(f"wd: {e}")

    # Signal 3 — optional Llama-Guard-4
    if use_llama_guard:
        try:
            flag = score_llama_guard(image_path)
            if flag is not None:  # None = disabled or clean
                detail = f"llama-guard {flag}"
                findings.append(GateFinding(GateSignal.LLAMA_GUARD_S1, True, detail))
        except Exception as e:
            findings.append(
                GateFinding(
                    GateSignal.MODEL_ERROR, triggered=True, detail=f"llama-guard error: {e}"
                )
            )
            errors.append(f"llama_guard: {e}")

    rejected = any(f.triggered for f in findings)
    return GateResult(
        verdict=GateVerdict.REJECT if rejected else GateVerdict.PASS,
        findings=findings,
        model_errors=errors,
    )


def gate_series_chapter(
    store,  # SeriesStore
    slug: str,
    chapter: int,
    *,
    enabled: bool = False,  # decision #023: gate REMOVED — pass-through by default
    civitai_scorer=None,  # default: module-level score_civitai (patchable in tests)
    wd_scorer=None,  # default: module-level score_wd_tagger
    use_llama_guard: bool = False,
) -> tuple[GateVerdict, str | None]:
    """Gate every page of a chapter; write verdict to chapter state + audit log.

    Decision #023 supersedes #013: the automated age gate is REMOVED (user
    reviews every title manually; user curates project intake). Default
    behaviour is now a pass-through: verdict=PASS, no scoring, panels advance.
    ``enabled=True`` restores the legacy fail-closed scoring (kept for
    opt-in use — nothing deleted).

    Legacy fail-closed rules (when enabled):
      - any page rejection = chapter REJECTED with that page's reason
      - a page whose source file is missing = model error = REJECT
      - panels of rejected pages stay in place (no downstream state changes)
      - panels of passed pages advance ORDERED = GATED (only panels that have
        been order-certified may advance; awaiting ones stay awaiting)
    """
    if not enabled:
        # pass-through: write the audit, advance certified panels, done
        pages = [p for p in store.load_pages(slug) if p.chapter == chapter]
        gdir = store.chapter_dir(slug, chapter)
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "gate_log.json").write_text(
            json.dumps(
                {
                    "chapter": chapter,
                    "verdict": "pass",
                    "reason": "gate removed (decision #023) — manual curation",
                    "pages": [
                        {"page": p.page_number, "verdict": "pass", "findings": []} for p in pages
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        store.set_chapter_gate(slug, chapter, True, None)
        for page in pages:
            for pn in page.panels:
                if pn.state in (PanelState.ORDERED, PanelState.SCRIPT_FAILED):
                    store.set_panel_state(slug, pn.panel_id, PanelState.GATED)
        return GateVerdict.PASS, None
    civitai_scorer = civitai_scorer or score_civitai
    wd_scorer = wd_scorer or score_wd_tagger
    sdir = store.series_dir(slug) / "source"
    pages = [p for p in store.load_pages(slug) if p.chapter == chapter]
    audit: list[dict] = []
    verdict = GateVerdict.PASS
    reason: str | None = None

    for page in pages:
        img_path = sdir / page.source_path
        if not img_path.exists():
            result = GateResult(
                GateVerdict.REJECT,
                findings=[GateFinding(GateSignal.MODEL_ERROR, True, "source file missing")],
                model_errors=["source file missing"],
            )
        else:
            result = gate_page(
                img_path,
                civitai_scorer=civitai_scorer,
                wd_scorer=wd_scorer,
                use_llama_guard=use_llama_guard,
            )
        audit.append(
            {
                "page": page.page_number,
                "source": page.source_path,
                "verdict": result.verdict.value,
                "findings": [vars(f) | {"signal": f.signal.value} for f in result.findings],
            }
        )
        if result.verdict is GateVerdict.REJECT:
            verdict = GateVerdict.REJECT
            joined = "; ".join(result.reject_reasons)
            reason = f"page {page.page_number}: {joined}"
            break  # fail-closed: one rejection ends the chapter gate

    # persist audit + chapter state
    gdir = store.chapter_dir(slug, chapter)
    gdir.mkdir(parents=True, exist_ok=True)
    gate_log = {
        "chapter": chapter,
        "verdict": str(verdict.value),
        "reason": reason,
        "pages": audit,
    }
    (gdir / "gate_log.json").write_text(json.dumps(gate_log, indent=2), encoding="utf-8")
    store.set_chapter_gate(slug, chapter, verdict is GateVerdict.PASS, reason)

    if verdict is GateVerdict.PASS:
        # advance certified panels (incl. SCRIPT_FAILED re-entries) → GATED
        for page in pages:
            for pn in page.panels:
                if pn.state in (PanelState.ORDERED, PanelState.SCRIPT_FAILED):
                    store.set_panel_state(slug, pn.panel_id, PanelState.GATED)
    return verdict, reason


def chapter_gate_summary(store, slug: str, chapter: int) -> dict:
    """Read back the persisted gate decision for status/CLI display."""
    path = store.chapter_dir(slug, chapter) / "gate_log.json"
    if not path.exists():
        return {"gated": False}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "gated": True,
        "verdict": data["verdict"],
        "reason": data["reason"],
        "pages": len(data["pages"]),
    }


__all__ = [
    "Chapter",
    "GateModelError",
    "GateResult",
    "GateVerdict",
    "Page",
    "chapter_gate_summary",
    "gate_page",
    "gate_series_chapter",
]
