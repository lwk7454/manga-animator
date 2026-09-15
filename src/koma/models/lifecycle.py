"""Panel lifecycle — legal transitions and helpers.

Every state transition is written to disk before any side effect (vidgen
doctrine): the store is updated first, then the work happens. A crash between
the two means a re-run skips finished work.
"""

from __future__ import annotations

from collections.abc import Iterator

from .domain import PanelState

# Ordered progression used for "how far along is this panel" reporting.
MAINLINE: tuple[PanelState, ...] = (
    PanelState.DETECTED,
    PanelState.AWAITING_ORDER_REVIEW,
    PanelState.ORDERED,
    PanelState.GATED,
    PanelState.AWAITING_CARD_REVIEW,
    PanelState.SCRIPTED,
    PanelState.COLORIZED,
    PanelState.KEYFRAMED,
    PanelState.QUEUED,
    PanelState.GENERATING,
    PanelState.GENERATED,
    PanelState.DOWNLOADED,
    PanelState.VALIDATED,
    PanelState.UPSCALED,
    PanelState.ASSEMBLED,
)

# Any state may fall to these two; they are the recovery/rework lanes.
FALLBACK_TO: frozenset[PanelState] = frozenset(
    {PanelState.FAILED, PanelState.NEEDS_REGEN, PanelState.SCRIPT_FAILED}
)

# Legal edges. Forward along the mainline (skip-ahead is legal — e.g. color
# sources skip COLORIZED; card-review may be skipped when no new cards), plus
# the rework edges: FAILED/NEEDS_REGEN -> QUEUED (re-queue, vidgen decision
# #32). Backward moves are NOT legal — rework goes through NEEDS_REGEN.
LEGAL_TRANSITIONS: dict[PanelState, frozenset[PanelState]] = {}
for _i, _state in enumerate(MAINLINE):
    LEGAL_TRANSITIONS[_state] = frozenset(MAINLINE[_i + 1 :]) | FALLBACK_TO
LEGAL_TRANSITIONS[PanelState.FAILED] = frozenset({PanelState.QUEUED, *FALLBACK_TO})
LEGAL_TRANSITIONS[PanelState.NEEDS_REGEN] = frozenset({PanelState.QUEUED, *FALLBACK_TO})
# SCRIPT_FAILED recovers by re-scripting (retry of the same step) or rework
LEGAL_TRANSITIONS[PanelState.SCRIPT_FAILED] = frozenset(
    {PanelState.SCRIPTED, PanelState.GATED, *FALLBACK_TO}
)

# Skip-forward is legal (e.g. color sources skip COLORIZED work: panels go
# GATED -> SCRIPTED -> KEYFRAMED with color.png = panel.png). Backward moves
# are NOT legal — rework goes through NEEDS_REGEN -> QUEUED instead.


def can_transition(current: PanelState, target: PanelState) -> bool:
    if current == target:
        return True  # idempotent re-runs
    return target in LEGAL_TRANSITIONS[current]


def next_state(current: PanelState) -> PanelState | None:
    """Immediate mainline successor, or None at the end / off-mainline."""
    try:
        idx = MAINLINE.index(current)
    except ValueError:
        return None
    return MAINLINE[idx + 1] if idx + 1 < len(MAINLINE) else None


def iter_mainline() -> Iterator[PanelState]:
    yield from MAINLINE
