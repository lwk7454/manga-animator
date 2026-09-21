"""Character color-sheet proposals via the qualified Qwen-Edit workflow (G1)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..storage.asset_store import AssetStore
from ..storage.errors import NotFound
from ..util import file_sha256, new_id, utcnow_iso

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / "config" / "workflows" / "qwen_image_edit_2509_colorize.json"

SHEET_PROMPT = (
    "Recolor this character crop into a clean full-color appearance sheet. "
    "Match visible clothing, hair, skin, and accessories. Do not preserve "
    "black-and-white screentone. Adults-only content must not be sanitized."
)


def load_qwen_edit_graph(
    *,
    lineart_name: str,
    ref_name: str,
    prompt: str = SHEET_PROMPT,
    seed: int = 42,
    width: int = 512,
    height: int = 512,
) -> dict[str, Any]:
    if not WORKFLOW.is_file():
        raise FileNotFoundError(WORKFLOW)
    graph = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    graph["1"]["inputs"]["value"] = prompt
    graph["200"]["inputs"]["image"] = lineart_name
    graph["201"]["inputs"]["image"] = ref_name
    graph["129"]["inputs"]["noise_seed"] = seed
    graph["115"]["inputs"]["width"] = width
    graph["115"]["inputs"]["height"] = height
    return graph


def propose_color_sheet(
    assets: AssetStore,
    *,
    series_id: str,
    display_name: str,
    sheet_path: Path,
    origin: str = "generated",
    source_panel_ids: list[str] | None = None,
) -> dict[str, Any]:
    if not sheet_path.is_file():
        raise NotFound(f"sheet file missing: {sheet_path}")
    sha = file_sha256(sheet_path)
    created = assets.create_asset(
        series_id,
        "character",
        display_name,
        importance="required",
        source_occurrences=source_panel_ids,
    )
    artifact_id = f"art:{new_id()}"
    now = utcnow_iso()
    root = assets.db.data_dir
    dest = root / "sheets" / created["asset_id"] / sheet_path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.resolve() != sheet_path.resolve():
        dest.write_bytes(sheet_path.read_bytes())
    relative = dest.relative_to(root).as_posix()
    with assets.db.transaction(immediate=True) as conn:
        conn.execute(
            "INSERT INTO artifacts (id, series_id, scope_key, kind, mode,"
            " relative_path, sha256, size, input_fingerprint, validation_status,"
            " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                artifact_id,
                series_id,
                f"sheet:{created['asset_id']}",
                "color_sheet",
                "production",
                relative,
                sha,
                dest.stat().st_size,
                sha,
                "validated",
                now,
            ),
        )
    revision = assets.add_revision(
        created["asset_id"],
        {
            "kind": "color_sheet",
            "status": "proposal",
            "generator": "qwen_image_edit_2509",
            "source_panel_ids": source_panel_ids or [],
        },
        origin=origin,
        artifact_ids=[artifact_id],
    )
    return {
        "asset_id": created["asset_id"],
        "revision": revision["revision"],
        "artifact_id": artifact_id,
        "sha256": sha,
        "state": "awaiting_review",
        "label": "PROPOSAL",
    }


__all__ = ["SHEET_PROMPT", "load_qwen_edit_graph", "propose_color_sheet"]
