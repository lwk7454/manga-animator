"""Series store — JSON-on-disk persistence.

Layout::

    <data_dir>/series/<slug>/
        series.json
        pages.json
        registry/characters.json
        chapters/<NN>/chapter.json
        chapters/<NN>/script.json        (script checkpoints)
        chapters/<NN>/panels/<PPPP>/...  (stage artifacts)
    <data_dir>/state/queue.json, paused  (current scheduler)

Idempotency rule (vidgen doctrine): state on disk is the source of truth.
All writes are atomic (temp file + rename). Derived paths are computed from
the slug — never stored.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ..models.domain import (
    Chapter,
    CharacterCard,
    Page,
    Panel,
    PanelState,
    ReadingDirection,
    Series,
    SeriesMeta,
    panel_key,
    utcnow,
)
from ..models.lifecycle import can_transition

SERIES_FILE = "series.json"
PAGES_FILE = "pages.json"
REGISTRY_FILE = "characters.json"


def _atomic_write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class SeriesStore:
    """Persistence + lifecycle mutations for one series tree."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.series_root = self.data_dir / "series"
        self.state_dir = self.data_dir / "state"

    # ── series CRUD ──────────────────────────────────────────────────────────

    def series_dir(self, slug: str) -> Path:
        return self.series_root / slug

    def exists(self, slug: str) -> bool:
        return (self.series_dir(slug) / SERIES_FILE).exists()

    def list_slugs(self) -> list[str]:
        if not self.series_root.exists():
            return []
        return sorted(
            p.name
            for p in self.series_root.iterdir()
            if p.is_dir() and (p / SERIES_FILE).exists()
        )

    def create_series(
        self,
        slug: str,
        title: str = "",
        direction: ReadingDirection = ReadingDirection.AUTO,
        language: str = "ja",
        ratios: list[str] | None = None,
    ) -> Series:
        if not slug or slug != slug.lower() or not slug.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                f"invalid slug {slug!r}: use lowercase letters, digits, '-' or '_'"
            )
        if self.exists(slug):
            raise FileExistsError(f"series {slug!r} already exists")
        meta = SeriesMeta(slug=slug, title=title, direction=direction, language=language)
        if ratios is not None:
            meta.ratios = ratios
        series = Series(meta=meta)
        sdir = self.series_dir(slug)
        sdir.mkdir(parents=True)
        self.save_series(series)
        (sdir / "registry").mkdir(exist_ok=True)
        (self.state_dir).mkdir(parents=True, exist_ok=True)
        self._write_registry(slug, [])  # seed empty registry
        return series

    def load_series(self, slug: str) -> Series:
        path = self.series_dir(slug) / SERIES_FILE
        if not path.exists():
            raise KeyError(f"No series {slug!r} at {path}")
        return Series.model_validate_json(path.read_text(encoding="utf-8"))

    def save_series(self, series: Series) -> None:
        _atomic_write_json(
            self.series_dir(series.meta.slug) / SERIES_FILE,
            json.loads(series.model_dump_json()),
        )

    # ── pages / panels (pages.json) ──────────────────────────────────────────

    def _pages_path(self, slug: str) -> Path:
        return self.series_dir(slug) / PAGES_FILE

    def load_pages(self, slug: str) -> list[Page]:
        path = self._pages_path(slug)
        if not path.exists():
            return []
        return [Page.model_validate(p) for p in json.loads(path.read_text(encoding="utf-8"))]

    def save_pages(self, slug: str, pages: list[Page]) -> None:
        _atomic_write_json(self._pages_path(slug), [json.loads(p.model_dump_json()) for p in pages])

    def all_panels(self, slug: str) -> list[Panel]:
        out: list[Panel] = []
        for page in self.load_pages(slug):
            out.extend(page.panels)
        return out

    def panels_in_state(self, slug: str, *states: PanelState) -> list[Panel]:
        wanted = set(states)
        return [p for p in self.all_panels(slug) if p.state in wanted]

    def find_panel(self, slug: str, panel_id: str) -> tuple[Page, Panel]:
        for page in self.load_pages(slug):
            for panel in page.panels:
                if panel.panel_id == panel_id:
                    return page, panel
        raise KeyError(f"panel {panel_id!r} not found in {slug!r}")

    def update_panel(self, slug: str, panel_id: str, **fields: object) -> Panel:
        """Immutable-field update: mutate a copy, validate, atomic-write back.

        ``None`` is a legal value for Optional fields (e.g. ``error=None``
        clears it); required fields must never receive None.
        """
        pages = self.load_pages(slug)
        for page in pages:
            for i, panel in enumerate(page.panels):
                if panel.panel_id == panel_id:
                    data = json.loads(panel.model_dump_json())
                    data.update(fields)
                    updated = Panel.model_validate(data)
                    page.panels[i] = updated
                    self.save_pages(slug, pages)
                    return updated
        raise KeyError(f"panel {panel_id!r} not found in {slug!r}")

    def set_panel_state(self, slug: str, panel_id: str, target: PanelState) -> Panel:
        """Transition with lifecycle enforcement (before any side effect)."""
        _, panel = self.find_panel(slug, panel_id)
        if not can_transition(panel.state, target):
            raise ValueError(
                f"illegal transition {panel.state.value} → {target.value} for {panel_id}"
            )
        return self.update_panel(slug, panel_id, state=target, updated_at=utcnow())

    # ── chapters ─────────────────────────────────────────────────────────────

    def chapter_dir(self, slug: str, chapter: int) -> Path:
        return self.series_dir(slug) / "chapters" / f"{chapter:02d}"

    def load_chapters(self, slug: str) -> list[Chapter]:
        return self.load_series(slug).chapters

    def ensure_chapter(self, slug: str, chapter: int) -> Chapter:
        series = self.load_series(slug)
        for ch in series.chapters:
            if ch.chapter_number == chapter:
                return ch
        ch = Chapter(chapter_number=chapter, state="pending")
        series.chapters.append(ch)
        series.chapters.sort(key=lambda c: c.chapter_number)
        self.save_series(series)
        self.chapter_dir(slug, chapter).mkdir(parents=True, exist_ok=True)
        return ch

    def update_chapter(self, slug: str, chapter: int, **fields: object) -> Chapter:
        series = self.load_series(slug)
        for i, ch in enumerate(series.chapters):
            if ch.chapter_number == chapter:
                data = json.loads(ch.model_dump_json())
                data.update(fields)
                updated = Chapter.model_validate(data)
                series.chapters[i] = updated
                self.save_series(series)
                return updated
        raise KeyError(f"chapter {chapter} not found in {slug!r}")

    def set_chapter_gate(
        self, slug: str, chapter: int, passed: bool, reason: str | None = None
    ) -> Chapter:
        return self.update_chapter(slug, chapter, gate_passed=passed, gate_reason=reason)

    # ── character registry (versioned target: PLAN.md §14) ────────────────────────────────

    def _registry_path(self, slug: str) -> Path:
        return self.series_dir(slug) / "registry" / REGISTRY_FILE

    def _write_registry(self, slug: str, cards: list[CharacterCard]) -> None:
        payload = [json.loads(c.model_dump_json()) for c in cards]
        _atomic_write_json(self._registry_path(slug), payload)

    def load_registry(self, slug: str) -> list[CharacterCard]:
        path = self._registry_path(slug)
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [CharacterCard.model_validate(c) for c in raw]

    def upsert_card(self, slug: str, card: CharacterCard) -> CharacterCard:
        cards = self.load_registry(slug)
        for i, existing in enumerate(cards):
            if existing.id == card.id:
                cards[i] = card
                break
        else:
            cards.append(card)
        self._write_registry(slug, cards)
        return card

    def find_card(self, slug: str, character_id: str) -> CharacterCard:
        for card in self.load_registry(slug):
            if card.id == character_id:
                return card
        raise KeyError(f"character {character_id!r} not in registry for {slug!r}")

    # ── order review (§12 contract on the interim JSON store) ────────────────
    #
    # Full desired permutation + expected revision + idempotent operation id.
    # Panel identity is NEVER rewritten (D03): sorting updates reading order
    # only. P04+ moves this onto the SQLite store; the behavior contract is
    # identical so tests carry over unchanged.

    def page_order_revision(self, slug: str, chapter: int, page_number: int) -> int:
        return int(self.load_order_meta(slug, chapter, page_number).get("revision", 0))

    def order_meta_path(self, slug: str, chapter: int, page_number: int) -> Path:
        return (
            self.chapter_dir(slug, chapter) / "order_meta" / f"{page_number:03d}.json"
        )

    def load_order_meta(self, slug: str, chapter: int, page_number: int) -> dict:
        path = self.order_meta_path(slug, chapter, page_number)
        if not path.exists():
            return {"revision": 0, "operations": {}}
        return json.loads(path.read_text(encoding="utf-8"))

    def set_page_order(
        self,
        slug: str,
        page_id: str,
        expected_revision: int,
        desired_panel_ids: list[str],
        operation_id: str,
    ) -> dict:
        """Full desired permutation with CAS + operation idempotency (§12).

        ``page_id`` here is the chapter-scoped page identity of the interim
        JSON store: ``"ch<NN>/p<NNN>"`` (also accepted: ``page_id`` equal to
        the page's source_path). Panel identity is never rewritten.
        """
        pages = self.load_pages(slug)
        page = next(
            (p for p in pages if p.source_path == page_id or str(p.page_number) == page_id),
            None,
        )
        if page is None:
            raise KeyError(f"page {page_id!r} not found")
        meta = self.load_order_meta(slug, page.chapter, page.page_number)
        if operation_id in meta.get("operations", {}):
            prior = meta["operations"][operation_id]
            return {"operation_id": operation_id, "idempotent": True, **prior}
        current_rev = int(meta.get("revision", 0))
        if current_rev != expected_revision:
            from ..storage.errors import RevisionConflict

            raise RevisionConflict(
                f"page order revision {expected_revision} != current {current_rev}"
            )
        active = {pn.panel_id for pn in page.panels}
        if sorted(active) != sorted(desired_panel_ids) or len(set(desired_panel_ids)) != len(
            desired_panel_ids
        ):
            raise ValueError("desired order must contain every active panel id exactly once")
        position = {pid: i for i, pid in enumerate(desired_panel_ids, start=1)}
        for pn in page.panels:
            pn.order = position[pn.panel_id]
        self.save_pages(slug, pages)
        meta["revision"] = current_rev + 1
        meta.setdefault("operations", {})[operation_id] = {
            "order_review_revision": current_rev + 1
        }
        _atomic_write_json(self.order_meta_path(slug, page.chapter, page.page_number), meta)
        return {"operation_id": operation_id, "order_review_revision": current_rev + 1,
                "idempotent": False}

    # ── scheduler state flags (§9; current file-backed queue) ─────────────

    @property
    def paused_flag(self) -> Path:
        return self.state_dir / "paused"

    def set_paused(self, paused: bool) -> None:
        if paused:
            self.paused_flag.parent.mkdir(parents=True, exist_ok=True)
            self.paused_flag.write_text("manual pause\n", encoding="utf-8")
        else:
            self.paused_flag.unlink(missing_ok=True)

    def is_paused(self) -> bool:
        return self.paused_flag.exists()

    # ── provenance (current job keys) ───────────────

    def job_key(self, slug: str, chapter: int, panel_id: str, ratio: str, ts: int) -> str:
        return f"{slug}_{chapter:02d}_{panel_id}_{ratio.replace(':', 'x')}_{ts}"

    def upload_name(self, panel_id: str, ratio: str, rand: str) -> str:
        return f"{panel_id}_{ratio.replace(':', 'x')}_kf_{rand}.png"


__all__ = [
    "PanelState",
    "ReadingDirection",
    "SeriesStore",
    "panel_key",
]
