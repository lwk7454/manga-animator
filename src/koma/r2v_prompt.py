"""Colored R2V / I2V prompt contracts (PLAN §A, line 75).

A B&W-preserving prompt is not colored-output evidence. Production prompts
must request recoloring from approved appearance evidence and must name every
manifest <Picture N> tag.
"""

from __future__ import annotations

from typing import Any

from .adapters.reference_manifest import ReferenceManifestError

FORBIDDEN_MONOCHROME = (
    "preserve black and white",
    "preserve b&w",
    "keep monochrome",
    "keep grayscale",
    "remain black and white",
    "do not recolor",
    "without color",
    "lineart only",
)

RECOLOR_MARKERS = (
    "recolor",
    "full color",
    "colored",
    "colour",
    "appearance evidence",
)

CHOREOGRAPHY_DEFAULT = (
    "natural-speed motion: breathing, weight shift, flex, thrust, sway; "
    "no long still periods; camera distance matches the panel staging"
)


def assert_colored_prompt(prompt: str) -> str:
    """Reject monochrome-preserving language; require explicit recolor intent."""
    lowered = prompt.lower()
    for phrase in FORBIDDEN_MONOCHROME:
        if phrase in lowered:
            raise ReferenceManifestError(
                f"B&W-preserving prompt is not colored evidence (PLAN line 75): {phrase!r}"
            )
    if not any(marker in lowered for marker in RECOLOR_MARKERS):
        raise ReferenceManifestError(
            "colored R2V prompt must request recoloring from the appearance sheet"
        )
    return prompt


def build_colored_r2v_prompt(
    manifest: list[dict[str, Any]],
    *,
    caption: str,
    choreography: str = CHOREOGRAPHY_DEFAULT,
) -> str:
    """Panel = story evidence; approved sheet = appearance evidence; recolor required."""
    parts: list[str] = []
    facts = " ".join(caption.split()) if caption else "visible panel action only; invent nothing"
    parts.append(f"Story facts from the source panel: {facts}.")
    for entry in manifest:
        tag = entry["prompt_tag"]
        role = entry["role"]
        if role == "story_composition":
            parts.append(
                f"{tag} is the source panel: story and composition evidence. "
                f"Recolor the entire scene from the approved colored character sheet; "
                f"do not preserve black-and-white or screentone."
            )
        elif role == "appearance_evidence":
            parts.append(
                f"{tag} is approved appearance evidence. Recolor hair, skin, clothing, "
                f"and accessories to match this colored sheet exactly."
            )
        elif role == "composition_r2v_ref":
            parts.append(
                f"{tag} is a ratio-specific colored staging image used only as an R2V "
                f"reference, not as an I2V first frame."
            )
        elif role.endswith("_identity"):
            entity = entry.get("entity") or "the bound asset"
            parts.append(f"{tag} is identity evidence for {entity}.")
        else:
            parts.append(f"{tag} is {role.replace('_', ' ')}")
    parts.append(choreography)
    prompt = " ".join(parts)
    return assert_colored_prompt(prompt)


def build_i2v_choreography_prompt(
    caption: str,
    *,
    choreography: str = CHOREOGRAPHY_DEFAULT,
) -> str:
    """I2V first-frame route: first frame already carries color; prompt is motion."""
    facts = " ".join(caption.split()) if caption else "visible staged action only"
    prompt = (
        f"Animate the colored first frame with natural-speed motion. "
        f"Story facts: {facts}. {choreography} "
        f"Keep the approved colors; do not revert to monochrome."
    )
    return assert_colored_prompt(prompt)


__all__ = [
    "CHOREOGRAPHY_DEFAULT",
    "assert_colored_prompt",
    "build_colored_r2v_prompt",
    "build_i2v_choreography_prompt",
]
