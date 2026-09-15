"""Durable feature tests; no live model services."""

from __future__ import annotations

from koma.models.domain import (
    PanelState,
)
from koma.models.lifecycle import (
    LEGAL_TRANSITIONS,
    MAINLINE,
    can_transition,
    next_state,
)


class TestLifecycle:
    def test_mainline_forward_is_legal(self) -> None:
        import itertools

        for a, b in itertools.pairwise(MAINLINE):
            assert can_transition(a, b), f"{a} → {b} must be legal"

    def test_skip_forward_is_legal(self) -> None:
        # color sources skip colorization: GATED → KEYFRAMED is legal
        assert can_transition(PanelState.GATED, PanelState.KEYFRAMED)

    def test_backward_is_illegal(self) -> None:
        assert not can_transition(PanelState.VALIDATED, PanelState.SCRIPTED)
        assert not can_transition(PanelState.UPSCALED, PanelState.DETECTED)

    def test_any_state_can_fail_or_need_regen(self) -> None:
        for state in MAINLINE:
            assert can_transition(state, PanelState.FAILED)
            assert can_transition(state, PanelState.NEEDS_REGEN)

    def test_failed_and_regen_requeue(self) -> None:
        assert can_transition(PanelState.FAILED, PanelState.QUEUED)
        assert can_transition(PanelState.NEEDS_REGEN, PanelState.QUEUED)

    def test_self_transition_is_idempotent(self) -> None:
        for state in PanelState:
            assert can_transition(state, state)

    def test_transition_table_covers_all_states(self) -> None:
        assert set(LEGAL_TRANSITIONS) == set(PanelState)

    def test_next_state_walks_mainline(self) -> None:
        assert next_state(PanelState.DETECTED) is PanelState.AWAITING_ORDER_REVIEW
        assert next_state(PanelState.ASSEMBLED) is None
