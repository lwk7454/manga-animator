"""Typed script substep payloads (PLAN.md §13.2) — versioned Pydantic models.

Every panel's script decomposes into independently checkpointed substeps:
caption, OCR lines, speech normalization, per-language translations keyed by
LINE ID, provisional speaker mentions, motion description, and event
proposals with symbolic timing anchors. Each successful substep becomes an
immutable typed artifact; completion predicates are separate concepts (a
single aggregate SCRIPTED value cannot mean all are satisfied).

Validation is strict: model output is DATA — schemas, references and bounds
are checked before persistence. Missing/blank translations for nonempty
source lines are incomplete (D12). No prompt content selects filesystem
paths or commands.
"""

from __future__ import annotations

import enum
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1

_LINE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def new_line_id(chapter: int, panel_id: str, index: int) -> str:
    """Stable line ID: ``ln:<panel_id>:<index>`` (immutable once minted)."""
    return f"ln:{panel_id}:{index}"


class CaptionSubstep(BaseModel):
    """Vision caption of the panel (appearance stays in the keyframe)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    panel_id: str
    text: str = Field(min_length=1)
    model_identity: str
    prompt_hash: str
    input_fingerprint: str


class OcrLine(BaseModel):
    """One dialogue line with immutable ID and optional manual correction."""

    model_config = ConfigDict(extra="forbid")

    line_id: str = Field(pattern=r"^ln:[^:]+:\d+$")
    source_text: str
    source_language: str = "ja"
    bbox: dict[str, int] | None = None  # evidence when available
    confidence: float = 1.0
    manual_text: str | None = None  # human correction; OCR is never overwritten

    @property
    def effective_text(self) -> str:
        return (self.manual_text or self.source_text).strip()


class OcrSubstep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    panel_id: str
    lines: list[OcrLine] = Field(default_factory=list)
    engine: str = "manga_ocr"

    @field_validator("lines")
    @classmethod
    def _unique_ids(cls, v: list[OcrLine]) -> list[OcrLine]:
        ids = [line.line_id for line in v]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate line ids in OCR substep")
        return v


class SpeechSubstep(BaseModel):
    """Speech normalization — SEPARATE from verbatim OCR (§13.2).

    Never overwrites OCR with an invented paraphrase: the spoken form is a
    distinct field and the source text is preserved.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    panel_id: str
    lines: list[dict] = Field(default_factory=list)  # [{line_id, spoken_text}]
    narration_lines: list[str] = Field(default_factory=list)
    no_speech: bool = False

    @field_validator("lines")
    @classmethod
    def _known_lines(cls, v: list[dict], info) -> list[dict]:
        return v


class TranslationEntry(BaseModel):
    """One line's translation in one target language, with its own status."""

    model_config = ConfigDict(extra="forbid")

    line_id: str = Field(pattern=r"^ln:[^:]+:\d+$")
    language: str
    text: str = ""
    status: Literal["valid", "retry_needed"] = "valid"
    dependency_hash: str = ""

    @field_validator("text", mode="after")
    @classmethod
    def _blank_needs_retry(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _status_matches_text(self) -> TranslationEntry:
        if self.status == "valid" and not self.text.strip():
            self.status = "retry_needed"
        return self


class TranslationSubstep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    panel_id: str = ""
    entries: list[TranslationEntry] = Field(default_factory=list)

    def complete_for(self, languages: list[str], source_lines: dict[str, str]) -> bool:
        """D12: every required line x language present, nonempty for nonempty
        source. Empty source dialogue requires no translation."""
        by_line: dict[str, dict[str, str]] = {}
        for e in self.entries:
            by_line.setdefault(e.line_id, {})[e.language] = e.text
        for line_id, source in source_lines.items():
            for lang in languages:
                got = (by_line.get(line_id, {}).get(lang) or "").strip()
                if source.strip() and not got:
                    return False
        return True


class SpeakerMention(BaseModel):
    """Provisional speaker evidence — a DISTINCT unresolved state (§13.3).

    Never a guessed approved character ID; discovery may propose, only the
    production review resolves.
    """

    model_config = ConfigDict(extra="forbid")

    line_id: str
    mention_id: str  # provisional, e.g. "girl_1"
    evidence: str = ""
    confidence: float = 0.0
    unresolved: bool = True


class SpeakerSubstep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    panel_id: str
    mentions: list[SpeakerMention] = Field(default_factory=list)
    explicit_marks: dict[str, str] = Field(
        default_factory=dict,
        description="line_id → 'narration' | 'no_speech' | approved narrator voice",
    )


class EventKind(enum.StrEnum):
    DIALOGUE = "dialogue"
    SFX = "sfx"
    AMBIENCE = "ambience"
    BGM_MOOD = "bgm_mood"


class EventProposal(BaseModel):
    """Timed-event proposal with a SYMBOLIC anchor (§13.4).

    Anchors resolve to integer sample positions only after TTS measurement.
    Cycles and missing references are rejected at resolution time.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(pattern=r"^ev:[^:]+:\d+$")
    kind: EventKind
    anchor: str  # e.g. "panel_start", "utterance:ln:...:start", "panel_end"
    offset_samples: int = 0
    payload: dict = Field(default_factory=dict)


class TimingSubstep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    panel_id: str
    events: list[EventProposal] = Field(default_factory=list)
    sample_rate: int = 48000


class PanelScript(BaseModel):
    """All substeps for one panel + independent completion predicates (§13.2)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    panel_id: str
    caption: CaptionSubstep | None = None
    ocr: OcrSubstep | None = None
    speech: SpeechSubstep | None = None
    translations: TranslationSubstep | None = None
    speakers: SpeakerSubstep | None = None
    motion_prompt: str | None = None
    motion_input_fingerprint: str | None = None
    events: TimingSubstep | None = None

    def source_lines(self) -> dict[str, str]:
        """line_id → effective source text (empty when no dialogue)."""
        if self.ocr is None:
            return {}
        return {line.line_id: line.effective_text for line in self.ocr.lines}

    def caption_ready(self) -> bool:
        return self.caption is not None and bool(self.caption.text.strip())

    def ocr_ready(self) -> bool:
        return self.ocr is not None

    def translations_ready(self, languages: list[str]) -> bool:
        if self.ocr is None:
            return False
        sub = self.translations or TranslationSubstep()
        return sub.complete_for(languages, self.source_lines())

    def speakers_ready(self) -> bool:
        if self.ocr is None or not self.ocr.lines:
            return True  # zero dialogue is explicit and complete
        if self.speech is not None and self.speech.no_speech:
            return True
        if not self.speech or not self.speech.lines:
            return False  # OCR lines exist but no normalization yet
        return all(
            not m.unresolved or m.mention_id
            for m in (self.speakers.mentions if self.speakers else [])
        )


class ChapterScript(BaseModel):
    """The typed script artifact: per-panel substeps + chapter-level predicate."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    series_slug: str
    chapter: int
    panels: dict[str, PanelScript] = Field(default_factory=dict)

    def missing_translations(self, languages: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for pid, panel in self.panels.items():
            if panel.translations_ready(languages):
                continue
            missing: list[str] = []
            if panel.ocr is None:
                missing.append("ocr")
            else:
                for lang in languages:
                    for line in panel.ocr.lines:
                        entries = (panel.translations.entries if panel.translations else [])
                        hit = [e for e in entries
                               if e.line_id == line.line_id and e.language == lang]
                        if not hit or not hit[0].text.strip():
                            missing.append(f"{line.line_id}:{lang}")
            if missing:
                out[pid] = missing
        return out
