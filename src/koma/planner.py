"""R4 — shot planner: typed local proposal from panels + script facts.

Deterministic: reads panel crops, reading order, script dialogue lines and
proposes beats + shots. Story-supported facts only; invented actions/
participants/outcomes are prohibited (§E). Proposal is typed, then validated
against the R1 stores; every generation input is exposed before dispatch.
"""

from __future__ import annotations

from typing import Any

from .storage.errors import InvalidTransition
from .storage.shot_store import ShotStore

DEFAULT_GRIDS = {"16:9": (864, 480), "9:16": (480, 864)}  # 32-grid model canvases


class PlanProposalError(RuntimeError):
    pass


def propose_beats_from_script(
    chapter_id: str,
    series_id: str,
    plan_revision: int,
    panels_in_order: list[dict[str, Any]],
    script_panels: dict[str, Any],
    shots: ShotStore,
) -> list[dict[str, Any]]:
    """One beat per panel with dialogue; panels without dialogue group forward
    into the next beat that HAS content. Causality = reading order."""
    beats = []
    idx = 0
    for panel in panels_in_order:
        pid = panel["panel_id"]
        doc = script_panels.get(pid) or {}
        lines = doc.get("dialogue") or doc.get("lines") or doc.get("ocr", {}).get("lines", [])
        line_ids = []
        for i, ln in enumerate(lines):
            if (
                ln.get("manual_text")
                or ln.get("source_text")
                or ln.get("jp")
                or ln.get("text")
                or ""
            ).strip():
                line_ids.append(ln.get("line_id") or f"ln:{pid}:{i}")
        idx += 1
        result = shots.add_beat(
            series_id,
            chapter_id,
            plan_revision,
            beat_index=idx,
            source_panel_ids=[pid],
            supporting_text=str(doc.get("caption") or "")[:200],
            participants=[],  # filled from bindings at review time
            dialogue_line_ids=line_ids,
        )
        result["dialogue_line_ids"] = line_ids
        result["source_panel_ids"] = [pid]
        beats.append(result)
    return beats


def propose_shots_from_beats(
    series_id: str,
    chapter_id: str,
    beats: list[dict[str, Any]],
    shots: ShotStore,
    *,
    max_beats_per_shot: int = 1,
) -> list[dict[str, Any]]:
    """Default planner: dialogue beats get their own shot; non-dialogue
    consecutive beats group up to max_beats_per_shot (several panels may
    form one shot — D18). Beat order preserved; no skipping."""
    if max_beats_per_shot < 1:
        raise PlanProposalError("max_beats_per_shot must be positive")
    proposals: list[dict[str, Any]] = []
    group: list[dict[str, Any]] = []
    for b in beats:
        group.append(b)
        has_dialogue = bool(b["dialogue_line_ids"])
        if has_dialogue or len(group) >= max_beats_per_shot:
            proposals.append(_emit(series_id, chapter_id, group, shots))
            group = []
    if group:
        proposals.append(_emit(series_id, chapter_id, group, shots))
    return proposals


def _emit(
    series_id: str,
    chapter_id: str,
    group: list[dict[str, Any]],
    shots: ShotStore,
    geometry: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    from .scenes import order_source_panels

    panel_ids: list[str] = []
    for b in group:
        panel_ids.extend(b["source_panel_ids"])
    panel_ids = order_source_panels(panel_ids, geometry)
    purpose = (
        "dialogue delivery"
        if any(b["dialogue_line_ids"] for b in group)
        else "transition/supporting movement"
    )
    return shots.add_shot_revision(
        series_id,
        chapter_id,
        beat_coverage=[b["beat_id"] for b in group],
        source_panel_ids=panel_ids,
        purpose=purpose,
        route="direct_r2v",
        camera={"size": "medium", "action": "locked"},
    )


def spec_ratios_for_shot(
    shots: ShotStore,
    shot_revision_id: str,
    ratio: str,
    source_panel_size: tuple[int, int],
    *,
    profile: str | None = None,
) -> dict[str, Any]:
    """Per-ratio composition: choose the 32-grid model canvas by source
    orientation, keep the source aspect approximately (soft reference)."""
    width, height = DEFAULT_GRIDS.get(ratio, (864, 480))
    if ratio not in DEFAULT_GRIDS:
        raise InvalidTransition(f"unsupported ratio {ratio} for spec")
    # EXPAND policy (owner directive): the scene evidence is fitted INSIDE
    # the delivery canvas — the model generates surroundings to fill the
    # rest. Panel content is never cropped away by ratio conversion.
    placement = {
        "scale": "fit_contain",
        "margin_pct": 0.05,
        "canvas_fill": "expand_environment",
        "crop_policy": "never_crop_panel_content",
    }
    return shots.add_ratio_spec(
        shot_revision_id,
        ratio,
        target_width=width,
        target_height=height,
        placement=placement,
        framing_note=f"canvas {width}x{height} for {ratio}; scene fitted, environment expanded",
        reference_manifest=[],  # filled from bindings at dispatch
        generation_profile=profile,
        dependency_fingerprint=fingerprint_spec(
            shot_revision_id, ratio, width, height, placement, profile
        ),
    )


def fingerprint_spec(
    shot_revision_id: str,
    ratio: str,
    width: int,
    height: int,
    placement: dict | None,
    profile: str | None,
) -> str:
    from .util import canonical_hash

    payload = {
        "shot_revision_id": shot_revision_id,
        "ratio": ratio,
        "width": width,
        "height": height,
        "placement": placement or {},
        "profile": profile,
    }
    return canonical_hash(payload)


def validate_no_invented_events(
    beats: list[dict[str, Any]],
    proposed: list[dict[str, Any]],
) -> None:
    """Reject unknown beat IDs. Semantic story fidelity still requires human review."""
    source_texts = {b["beat_id"]: b["supporting_text"] for b in beats}
    for p in proposed:
        for bid in p.get("beat_coverage", []):
            if bid not in source_texts:
                raise PlanProposalError(f"shot references unknown beat {bid}")


def source_panel_ids_str(panel_ids: list[str]) -> str:
    return ", ".join(panel_ids[:3]) + ("…" if len(panel_ids) > 3 else "")


__all__ = [
    "DEFAULT_GRIDS",
    "PlanProposalError",
    "fingerprint_spec",
    "propose_beats_from_script",
    "propose_shots_from_beats",
    "source_panel_ids_str",
    "validate_no_invented_events",
]
