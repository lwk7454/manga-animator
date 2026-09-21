"""Composition-assisted I2V compiler (first_frame only; no R2V ref sockets)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .application.composition import ROLE_I2V_FIRST_FRAME
from .r2v_compiler import CompilerError
from .r2v_prompt import build_i2v_choreography_prompt
from .storage.errors import InvalidTransition

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "config" / "workflows" / "minimax_h3_i2v_turbo_v2.json"


def load_i2v_graph() -> dict[str, Any]:
    if not WORKFLOW.is_file():
        raise CompilerError(f"missing I2V workflow {WORKFLOW}")
    return json.loads(WORKFLOW.read_text(encoding="utf-8"))


def compile_i2v_graph(
    *,
    first_frame_upload: str,
    prompt: str,
    width: int,
    height: int,
    frames: int,
    seed: int,
) -> dict[str, Any]:
    if width <= 0 or height <= 0 or width % 32 or height % 32:
        raise CompilerError("canvas must use positive multiples of 32")
    graph = load_i2v_graph()
    graph["6"]["inputs"]["image"] = first_frame_upload
    inputs = graph["7"]["inputs"]
    if "audio_vae" in inputs or "ref_images" in inputs:
        raise InvalidTransition("I2V node must not receive R2V-only inputs")
    if "first_frame" not in inputs:
        raise CompilerError("I2V workflow lacks first_frame")
    inputs["prompt"] = prompt
    inputs["width"] = width
    inputs["height"] = height
    inputs["length"] = frames
    graph["8"]["inputs"]["noise_seed"] = seed
    return graph


def i2v_prompt_for_caption(caption: str) -> str:
    return build_i2v_choreography_prompt(caption)


def require_first_frame_role(manifest: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [m for m in manifest if m["role"] == ROLE_I2V_FIRST_FRAME]
    if len(matches) != 1:
        raise InvalidTransition("composition I2V requires exactly one first-frame artifact")
    if any(m["role"] == "composition_r2v_ref" for m in manifest):
        raise InvalidTransition("never relabel an R2V composition ref as an I2V first frame")
    return matches[0]


__all__ = [
    "compile_i2v_graph",
    "i2v_prompt_for_caption",
    "load_i2v_graph",
    "require_first_frame_role",
]
