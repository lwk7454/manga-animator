"""Domain models — Series, Chapter, Page, Panel, CharacterCard, Job.

State on disk is the source of truth; every model is pydantic v2 and
JSON-roundtrippable. All transitions go through :mod:`koma.models.lifecycle`.
"""

from koma.models.domain import (
    Chapter,
    CharacterCard,
    Job,
    JobStatus,
    Page,
    Panel,
    PanelState,
    ReadingDirection,
    Series,
    SeriesMeta,
    VoiceSpec,
    panel_key,
)
from koma.models.lifecycle import LEGAL_TRANSITIONS, can_transition, next_state

__all__ = [
    "LEGAL_TRANSITIONS",
    "Chapter",
    "CharacterCard",
    "Job",
    "JobStatus",
    "Page",
    "Panel",
    "PanelState",
    "ReadingDirection",
    "Series",
    "SeriesMeta",
    "VoiceSpec",
    "can_transition",
    "next_state",
    "panel_key",
]
