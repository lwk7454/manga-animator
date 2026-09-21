"""Ratio-specific colored compositions and exclusive adapter roles (G2)."""

from __future__ import annotations

from typing import Any

from ..delivery import RATIO_CANVAS
from ..storage.errors import InvalidTransition

ROLE_R2V_REF = "composition_r2v_ref"
ROLE_I2V_FIRST_FRAME = "composition_i2v_first_frame"
COMPOSITION_ROLES = frozenset({ROLE_R2V_REF, ROLE_I2V_FIRST_FRAME})
DEFAULT_COMPOSITION_ROLE = ROLE_I2V_FIRST_FRAME


def assert_exclusive_roles(manifest: list[dict[str, Any]]) -> None:
    present = [m["role"] for m in manifest if m["role"] in COMPOSITION_ROLES]
    if len(present) > 1:
        raise InvalidTransition(
            "composition roles are exclusive; never relabel R2V-ref as I2V first-frame"
        )


def composition_entry(
    *,
    artifact_id: str,
    sha256: str,
    role: str,
    ratio: str,
    slot_offset: int = 1,
) -> dict[str, Any]:
    if role not in COMPOSITION_ROLES:
        raise InvalidTransition(f"unknown composition role {role}")
    if ratio not in RATIO_CANVAS:
        raise InvalidTransition(f"unsupported composition ratio {ratio}")
    width, height = RATIO_CANVAS[ratio]
    return {
        "slot_key": f"ref_image_{slot_offset}" if role == ROLE_R2V_REF else "first_frame",
        "prompt_tag": f"<Picture {slot_offset + 1}>" if role == ROLE_R2V_REF else "",
        "role": role,
        "entity": None,
        "artifact_id": artifact_id,
        "sha256": sha256,
        "ratio": ratio,
        "target_width": width,
        "target_height": height,
    }


def select_route(*, use_composition: bool, adapter_role: str | None = None) -> dict[str, str]:
    if not use_composition:
        return {"route": "direct_r2v", "adapter_role": ""}
    role = adapter_role or DEFAULT_COMPOSITION_ROLE
    if role not in COMPOSITION_ROLES:
        raise InvalidTransition(f"unknown adapter role {role}")
    return {"route": "composition_assisted", "adapter_role": role}


__all__ = [
    "COMPOSITION_ROLES",
    "DEFAULT_COMPOSITION_ROLE",
    "ROLE_I2V_FIRST_FRAME",
    "ROLE_R2V_REF",
    "assert_exclusive_roles",
    "composition_entry",
    "select_route",
]
