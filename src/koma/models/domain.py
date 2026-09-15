"""Domain entities (PLAN §6 state model, §7 registry)."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

# ── enums ────────────────────────────────────────────────────────────────────


class ReadingDirection(enum.StrEnum):
    """Page reading direction (decision #005)."""

    RTL = "rtl"  # JP manga: right-to-left, top-to-bottom
    LTR = "ltr"  # manhwa / western: left-to-right, top-to-bottom
    AUTO = "auto"  # heuristic decides at ingest; resolved to RTL/LTR per page


class PanelState(enum.StrEnum):
    """Panel lifecycle (PLAN §6). Review-gate states are part of the flow."""

    DETECTED = "detected"
    AWAITING_ORDER_REVIEW = "awaiting_order_review"
    ORDERED = "ordered"
    GATED = "gated"
    AWAITING_CARD_REVIEW = "awaiting_card_review"
    SCRIPTED = "scripted"
    COLORIZED = "colorized"
    KEYFRAMED = "keyframed"
    QUEUED = "queued"
    GENERATING = "generating"
    GENERATED = "generated"
    DOWNLOADED = "downloaded"
    VALIDATED = "validated"
    UPSCALED = "upscaled"
    ASSEMBLED = "assembled"
    FAILED = "failed"
    NEEDS_REGEN = "needs_regen"
    SCRIPT_FAILED = "script_failed"


class JobStatus(enum.StrEnum):
    """GPU job lifecycle, mirrors vidgen's JobStatus semantics."""

    PENDING = "pending"
    QUEUED = "queued"
    GENERATING = "generating"
    GENERATED = "generated"
    DOWNLOADED = "downloaded"
    FAILED = "failed"


# ── helpers ──────────────────────────────────────────────────────────────────


def utcnow() -> datetime:
    return datetime.now(UTC)


def panel_key(chapter: int, page: int, panel: int) -> str:
    """Canonical panel id, e.g. ``ch01_p003_02``."""
    return f"ch{chapter:02d}_p{page:03d}_{panel:02d}"


# ── entities ─────────────────────────────────────────────────────────────────


class SeriesMeta(BaseModel):
    """Series-level settings (series.json)."""

    slug: str
    title: str = ""
    direction: ReadingDirection = ReadingDirection.AUTO
    language: str = "ja"  # source language
    voice_language: str = "ja"  # decision #002: independent of subtitles
    subtitle_languages: list[str] = Field(default_factory=lambda: ["en", "zh-HANS"])
    ratios: list[str] = Field(default_factory=lambda: ["16:9", "9:16"])
    source_type: Literal["jpeg", "pdf"] = "jpeg"
    is_color_source: bool = False  # B&W sources get colorized (decision #004)
    video_profile: str | None = None  # per-series pin (decision #020); None = default
    created_at: datetime = Field(default_factory=utcnow)


class Series(BaseModel):
    meta: SeriesMeta
    chapters: list[Chapter] = Field(default_factory=list)


class BBox(BaseModel):
    """Pixel bbox on the source page image: x, y, w, h (origin top-left)."""

    x: int
    y: int
    w: int
    h: int

    def contains(self, other: BBox) -> bool:
        return (
            self.x <= other.x
            and self.y <= other.y
            and self.x + self.w >= other.x + other.w
            and self.y + self.h >= other.y + other.h
        )


class Page(BaseModel):
    """One source page and its detected panels."""

    chapter: int
    page_number: int
    source_path: str  # relative to series source/ dir, posix-style
    width: int = 0
    height: int = 0
    direction_resolved: ReadingDirection | None = None  # for AUTO series
    panels: list[Panel] = Field(default_factory=list)


class Panel(BaseModel):
    """Atomic animatable unit — one crop, one lifecycle (PLAN §2, §6)."""

    panel_id: str  # canonical: panel_key(chapter, page, panel)
    bbox: BBox
    order: int | None = None  # certified reading position within chapter
    state: PanelState = PanelState.DETECTED
    character_ids: list[str] = Field(default_factory=list)  # from script stage
    variant_ids: dict[str, str] = Field(default_factory=dict)  # char -> variant (decision #006)
    card_version_used: dict[str, int] = Field(default_factory=dict)  # char -> version
    prompt_id: str | None = None  # ComfyUI prompt_id when queued
    error: str | None = None
    retries: int = 0
    updated_at: datetime = Field(default_factory=utcnow)


class Chapter(BaseModel):
    chapter_number: int
    state: Literal[
        "pending",
        "ingested",
        "reviewed",
        "gated",
        "script_incomplete",
        "scripted",
        "rendering",
        "complete",
    ]
    pages: list[Page] = Field(default_factory=list)
    gate_passed: bool | None = None  # decision #013: None = not yet run
    gate_reason: str | None = None


class VoiceSpec(BaseModel):
    """Character voice binding (PLAN §7)."""

    engine: str = "gpt_sovits"
    ref_clip: str  # path relative to registry/assets/
    fallback_engine: str | None = "indextts25"


class CharacterVariant(BaseModel):
    """One outfit variant (decision #006: living registry, append variants)."""

    id: str
    version: int = 1
    sheet: str  # path relative to registry/assets/
    first_seen: str  # "ch01/p003"
    palette: dict[str, str] = Field(default_factory=dict)  # proposed colors


class CharacterCard(BaseModel):
    """Registry entry — the living anchor (PLAN §7)."""

    id: str
    version: int = 1
    identity: dict[str, str] = Field(default_factory=dict)  # descriptors
    voice: VoiceSpec | None = None
    variants: list[CharacterVariant] = Field(default_factory=list)
    review_status: Literal["pending", "approved", "rejected"] = "pending"
    updated_at: datetime = Field(default_factory=utcnow)


class Job(BaseModel):
    """One queued GPU operation (PLAN §10 job keys, vidgen collision rules)."""

    job_id: str  # "<series>_<chapter>_<panel>_<ratio>_<ts>"
    panel_id: str
    ratio: str
    kind: str  # colorize | keyframe | i2v | tts | upscale
    status: JobStatus = JobStatus.PENDING
    prompt_id: str | None = None
    output_path: str | None = None
    error: str | None = None
    retries: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


Chapter.model_rebuild()
Page.model_rebuild()
