"""R3 — multi-reference R2V compiler for production shots (PLAN §E, D14).

Builds an API-format graph from a shot's ratio spec + reference manifest,
reusing the exact graph contracts verified by the qualified probe
(`minimax_h3_panel_r2v_turbo4.api.json` node topology, ref2va weights,
official ref2v 4-step LoRA). The probe remains the qualification entrypoint;
this module is the production compiler it qualified the recipe for.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .adapters.reference_manifest import ReferenceManifestError, validate_manifest_against_capacity
from .r2v_probe import digest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT / "config" / "workflows"


class CompilerError(RuntimeError):
    pass


def load_base_graph(variant: str = "turbo4") -> dict[str, Any]:
    path = WORKFLOW_DIR / f"minimax_h3_panel_r2v_{variant}.api.json"
    if not path.is_file():
        raise CompilerError(f"missing base workflow {path}")
    return json.loads(path.read_text())


def compile_shot_graph(
    *,
    variant: str,
    panel_path: Path,
    reference_manifest: list[dict[str, Any]],
    prompt: str,
    width: int,
    height: int,
    frames: int,
    seed: int,
    upload_names: dict[str, str],
) -> dict[str, Any]:
    """Build the /prompt graph for one shot generation.

    ``upload_names`` maps artifact_id → uploaded ComfyUI input filename
    (uploads happen before compile; the caller owns transport).
    The panel occupies slot ``edit_image``; each manifest ref becomes
    ``ref_images.ref_image_<n>`` (zero-based socket, one-based prompt tag —
    the pairing verified live by the qualified probe).
    """
    graph = load_base_graph(variant)
    validate_manifest_against_capacity(reference_manifest)
    digest(panel_path)  # exists + readable

    # The qualified direct-panel graph wires the PANEL as ref_image_0
    # (prompt tag <Picture 1>): the panel is the first reference, composition
    # comes from it as a soft reference — no edit_image / still-guide.
    panel_name = upload_names.get(panel_artifact_key(reference_manifest))
    if not panel_name:
        raise CompilerError("panel not uploaded: no upload name for the panel slot")
    template_panel = graph["136"]["inputs"].get("ref_images.ref_image_0")
    if not template_panel:
        raise CompilerError("base graph lacks the panel ref slot ref_images.ref_image_0")
    panel_node = template_panel[0]
    graph[panel_node]["inputs"]["image"] = panel_name

    # identity refs continue at ref_image_1 → <Picture 2>, ... up to 8 extra
    ref_inputs: dict[str, list] = {"ref_image_0": [panel_node, 0]}
    slot = 1
    for m in reference_manifest:
        if m["role"] == "story_composition":
            continue
        node_key = f"Lref_image_{slot}"
        graph[node_key] = {
            "class_type": "LoadImage",
            "inputs": {"image": upload_names.get(m["artifact_id"], "")},
        }
        ref_inputs[f"ref_image_{slot}"] = [node_key, 0]
        slot += 1
    graph["136"]["inputs"]["ref_images"] = ref_inputs

    graph["136"]["inputs"].update(prompt=prompt, width=width, height=height,
                                  length=frames)
    graph["129"]["inputs"]["noise_seed"] = seed
    return graph


def panel_artifact_key(manifest: list[dict[str, Any]]) -> str:
    entry = next((m for m in manifest if m["role"] == "story_composition"), None)
    if entry is None:
        raise ReferenceManifestError("manifest lacks the panel (story_composition) entry")
    return entry["artifact_id"]


def reference_upload_plan(manifest: list[dict[str, Any]], panel_path: Path) -> list[dict[str, Any]]:
    """Ordered upload plan: (local_path, upload_name) with unique names."""
    plan = []
    seen_names: set[str] = set()
    for m in manifest:
        artifact = m["artifact_id"]
        name = f"koma_{artifact.replace(':', '_')}.png"
        if name in seen_names:
            raise CompilerError(f"duplicate upload name {name}")
        seen_names.add(name)
        plan.append({
            "artifact_id": m["artifact_id"],
            "upload_name": name,
            "role": m["role"],
            "is_panel": m["slot_key"] == "edit_image",
        })
    _ = panel_path  # panel path validated by digest in compile
    return plan


__all__ = ["CompilerError", "compile_shot_graph", "load_base_graph",
           "panel_artifact_key", "reference_upload_plan"]
