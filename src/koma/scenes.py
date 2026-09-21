"""G4 — group fragment panels into a scene; primary = most complete crop."""

from __future__ import annotations


def pick_primary_panel(panel_ids: list[str], geometry: dict[str, dict[str, int]] | None) -> str:
    if not panel_ids:
        raise ValueError("shot requires at least one source panel")
    if not geometry:
        return panel_ids[0]

    def area(pid: str) -> tuple[int, int]:
        box = geometry.get(pid) or {}
        w = int(box.get("bbox_w") or box.get("w") or 0)
        h = int(box.get("bbox_h") or box.get("h") or 0)
        return (w * h, -panel_ids.index(pid))

    return max(panel_ids, key=area)


def order_source_panels(
    panel_ids: list[str], geometry: dict[str, dict[str, int]] | None
) -> list[str]:
    primary = pick_primary_panel(panel_ids, geometry)
    return [primary, *[pid for pid in panel_ids if pid != primary]]


__all__ = ["order_source_panels", "pick_primary_panel"]
