"""Typed script substep store — immutable per-substep artifacts (PLAN §13).

Each successful substep is checkpointed IMMEDIATELY as its own immutable
JSON file under ``chapters/<NN>/script/<panel_id>/<substep>.json`` with a
content fingerprint. On pause, finish/checkpoint the current substep. On
process loss, only uncheckpointed substeps may repeat. Unaffected substeps
are reused when their input fingerprint is unchanged (D12; partial reuse).

The legacy whole-panel ``script.json`` remains a compatibility READER
(derived view, never written here) while existing consumers migrate.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..log import get_logger
from ..models.script import (
    ChapterScript,
    PanelScript,
)
from ..util import canonical_hash, utcnow_iso

log = get_logger("steps.script_store")

SUBSTEP_ORDER = ("caption", "ocr", "speech", "translations", "speakers", "timing")


class ScriptStore:
    """Filesystem artifact store for typed script substeps (immutable)."""

    def __init__(self, chapter_dir: Path):
        self.root = chapter_dir / "script"

    def panel_dir(self, panel_id: str) -> Path:
        return self.root / panel_id

    def substep_path(self, panel_id: str, substep: str) -> Path:
        return self.panel_dir(panel_id) / f"{substep}.json"

    def checkpoint(self, panel_id: str, substep: str, payload: dict) -> dict:
        """Persist ONE substep atomically with its content fingerprint."""
        pdir = self.panel_dir(panel_id)
        pdir.mkdir(parents=True, exist_ok=True)
        payload = {**payload, "checkpointed_at": utcnow_iso()}
        body = dict(payload)
        body.pop("checkpointed_at", None)
        body.pop("fingerprint", None)
        fp = canonical_hash(body)
        doc = {**body, "fingerprint": fp}
        tmp = pdir / f".{substep}.tmp"
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.substep_path(panel_id, substep))
        return {"substep": substep, "fingerprint": fp, "path": str(tmp.parent / f"{substep}.json")}

    def load(self, panel_id: str, substep: str) -> dict | None:
        p = self.substep_path(panel_id, substep)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def load_panel(self, panel_id: str, substeps: list[str] | None = None) -> dict:
        want = list(substeps or SUBSTEP_ORDER)
        out: dict[str, dict] = {}
        for s in want:
            doc = self.load(panel_id, s)
            if doc is not None:
                out[s] = doc
        return out

    def panel_fingerprint(self, panel_id: str, substep: str) -> str | None:
        doc = self.load(panel_id, substep)
        return doc.get("fingerprint") if doc else None


_STORE_KEYS = {"fingerprint", "checkpointed_at"}


def _strip_store_keys(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    return {k: v for k, v in doc.items() if k not in _STORE_KEYS}


def load_typed_script(chapter_dir: Path, slug: str, chapter: int) -> ChapterScript:
    """Derive the typed ChapterScript view from checkpointed substep artifacts."""
    store = ScriptStore(chapter_dir)
    panels: dict[str, PanelScript] = {}
    if not store.root.exists():
        return ChapterScript(series_slug=slug, chapter=chapter, panels={})
    for pdir in sorted(store.root.iterdir()):
        if not pdir.is_dir():
            continue
        pid = pdir.name
        doc = store.load_panel(pid)
        panels[pid] = PanelScript.model_validate({
            "panel_id": pid,
            "caption": _strip(doc.get("caption")),
            "ocr": _strip(doc.get("ocr")),
            "speech": _strip(doc.get("speech")),
            "translations": _strip(doc.get("translations")),
            "speakers": _strip(doc.get("speakers")),
            "events": _strip(doc.get("timing")),
            "motion_prompt": (doc.get("motion") or {}).get("text"),
            "motion_input_fingerprint": (doc.get("motion") or {}).get("input_fingerprint"),
        })
    return ChapterScript(series_slug=slug, chapter=chapter, panels=panels)


def _strip(doc: dict | None) -> dict | None:
    return _strip_store_keys(doc)


__all__ = ["SUBSTEP_ORDER", "ScriptStore", "load_typed_script"]
