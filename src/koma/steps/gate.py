"""Compatibility transition only. Automated content scoring has been removed.

New runs can script ORDERED panels directly after manual order review.
"""

from enum import StrEnum

from ..models.domain import PanelState


class GateVerdict(StrEnum):
    PASS = "pass"


def gate_series_chapter(store, slug: str, chapter: int) -> tuple[GateVerdict, str]:
    """Advance reviewed panels without model calls; never reset failed work."""
    pages = [page for page in store.load_pages(slug) if page.chapter == chapter]
    if not pages:
        raise ValueError(f"chapter {chapter} has no imported pages")
    for page in pages:
        for panel in page.panels:
            if panel.state is PanelState.ORDERED:
                store.set_panel_state(slug, panel.panel_id, PanelState.GATED)
    return GateVerdict.PASS, "automated scoring removed; manual review only"
