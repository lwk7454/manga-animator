"""Compatibility bridge and explicit local crop editing/replay commands."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from ..crops import Annotation, Mask, atomic_write, digest, export_page, png_bytes, source_pixels
from ..storage.crop_store import CropStore
from ..storage.domain_store import KomaStore
from ..util import utcnow_iso


def sync_crops(legacy, slug: str, pages: list) -> None:
    """Register legacy pages once, then always replay the canonical DB annotations."""
    from ..steps.crop_proposals import propose_masks

    store = KomaStore.open(legacy.data_dir)
    try:
        if store.get_series_by_slug(slug) is None:
            meta = legacy.load_series(slug).meta
            store.create_series(
                slug, title=meta.title, language=meta.language, required_ratios=meta.ratios
            )
        crops = CropStore(store.db)
        for page in pages:
            store.ensure_chapter(slug, page.chapter)
            source = legacy.series_dir(slug) / "source" / page.source_path
            sha = digest(source.read_bytes())
            existing = next(
                (
                    p
                    for p in store.load_pages(slug, page.chapter)
                    if p["source_path"] == page.source_path
                ),
                None,
            )
            if existing is None:
                store.import_pages(
                    slug,
                    page.chapter,
                    [
                        {
                            "source_path": page.source_path,
                            "sha256": sha,
                            "width": page.width,
                            "height": page.height,
                            "direction": (page.direction_resolved or "rtl"),
                            "panels": [
                                {
                                    "panel_id": p.panel_id,
                                    "bbox": p.bbox.model_dump(),
                                    "reading_order": p.order or i,
                                }
                                for i, p in enumerate(page.panels, 1)
                            ],
                        }
                    ],
                    operation_id=f"crop-import:{slug}:{page.source_path}:{sha}",
                    import_source_id="legacy-ingest-crops",
                )
                existing = next(
                    p
                    for p in store.load_pages(slug, page.chapter)
                    if p["source_path"] == page.source_path
                )
            if existing["source_sha256"] != sha:
                raise ValueError("source differs from SQLite page; explicit replacement required")
            current = crops.latest(existing["id"])
            if current is None:
                annotation = propose_masks(
                    source, page.panels, direction=str(page.direction_resolved or "rtl")
                )
                crops.submit(existing["id"], annotation, expected_revision=0)
            replay_page(store, slug, page.chapter, existing["id"])
    finally:
        store.db.close()


def replay_page(store: KomaStore, slug: str, chapter: int, page_id: str) -> dict:
    page = next((p for p in store.load_pages(slug, chapter) if p["id"] == page_id), None)
    if page is None:
        raise ValueError("page is not in this series/chapter")
    current = CropStore(store.db).latest(page_id)
    if current is None:
        raise ValueError("page has no mask annotations; ingest it first")
    annotation = Annotation.model_validate_json(current["payload"])
    root = store.db.data_dir / "series" / slug
    source = root / "source" / page["source_path"]
    out = root / "chapters" / f"{chapter:02d}" / "panels"
    entries = export_page(source, annotation, out)
    # A manifest is a derived snapshot. Never import it implicitly on replay.
    index_path = out / "panels_index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    index[source.name] = entries
    index.setdefault("_pages", {})[source.name] = {
        "page_w": annotation.width,
        "page_h": annotation.height,
    }
    index["_schema_version"] = 2
    index.setdefault("_annotations", {})[source.name] = {
        "page_id": page_id,
        "revision": current["revision"],
        "annotation_hash": current["annotation_hash"],
        "decision_status": current["decision_status"],
        "path": f"masks/{source.name}/{current['annotation_hash']}/annotations.json",
    }
    for entry in entries.values():
        entry["needs_review"] = current["decision_status"] != "approved"
    atomic_write(index_path, json.dumps(index, sort_keys=True, indent=2).encode())
    # Mirror geometry for old readers only; these fields never write back to DB.
    from ..models.domain import BBox
    from ..storage.series_store import SeriesStore

    legacy = SeriesStore(store.db.data_dir)
    pages = legacy.load_pages(slug)
    for old_page in pages:
        if old_page.source_path != page["source_path"] or old_page.chapter != chapter:
            continue
        for panel in old_page.panels:
            entry = entries[panel.panel_id]
            panel.bbox = BBox(**{k: entry[f"bbox_{k}"] for k in ("x", "y", "w", "h")})
            panel.order = entry["order"]
            for key in ("mask_path", "crop_path", "ownership_hash", "needs_review"):
                setattr(panel, key, entry[key])
    if pages:
        legacy.save_pages(slug, pages)
    if current["decision_status"] == "approved":
        _register_crops(store, page, current, out, entries)
    return index["_annotations"][source.name]


def _register_crops(store, page: dict, current: dict, out: Path, entries: dict) -> None:
    prepared = []
    for pid, entry in entries.items():
        path = out / entry["crop_path"]
        data = path.read_bytes()
        key = digest(f"{page['id']}:{current['revision']}:{pid}".encode())
        prepared.append((
            f"crop:{key}", page["series_id"], pid, f"crop:{key}",
            str(path.relative_to(store.db.data_dir)), digest(data), len(data),
            current["annotation_hash"], json.dumps({"mode": "RGBA", "format": "PNG"}),
            json.dumps({"annotation_hash": current["annotation_hash"],
                        "crop_revision": current["revision"]}), utcnow_iso(),
        ))
    with store.db.transaction(immediate=True) as conn:
        latest = conn.execute(
            "SELECT c.revision, r.decision_status FROM crop_annotations c "
            "JOIN reviews r ON r.id=c.review_id WHERE page_id=? "
            "ORDER BY revision DESC LIMIT 1", (page["id"],),
        ).fetchone()
        if latest["revision"] != current["revision"] or latest["decision_status"] != "approved":
            raise ValueError("crop approval changed during export; replay the current revision")
        for row in prepared:
            conn.execute(
                "INSERT OR IGNORE INTO artifacts (id,series_id,scope_key,kind,"
                "deterministic_operation_id,relative_path,sha256,size,input_fingerprint,"
                "media_metadata,result_metadata,validation_status,created_at) "
                "VALUES (?,?,?,'panel',?,?,?,?,?,?,?,'validated',?)", row,
            )


def edit_checkout(store: KomaStore, page_id: str, target: Path) -> None:
    row = CropStore(store.db).latest(page_id)
    if row is None:
        raise ValueError("no crop annotation")
    annotation = Annotation.model_validate_json(row["payload"])
    if target.exists() and any(target.iterdir()):
        raise ValueError("checkout directory must be empty; do not overwrite edits")
    atomic_write(target / "annotations.json", annotation.canonical_bytes())
    atomic_write(target / "revision.txt", str(row["revision"]).encode())
    for item in [*annotation.panels, *annotation.components]:
        mask = item.mask.array(annotation.width, annotation.height)
        atomic_write(
            target / f"{item.id}.png", png_bytes(Image.fromarray(mask.astype("uint8") * 255))
        )


def submit_edit(store: KomaStore, page_id: str, target: Path, expected_revision: int) -> int:
    annotation = Annotation.model_validate_json((target / "annotations.json").read_text())
    for item in [*annotation.panels, *annotation.components]:
        path = target / f"{item.id}.png"
        with Image.open(path) as im:
            if im.mode != "L" or im.size != (annotation.width, annotation.height):
                raise ValueError(f"{path}: expected page-sized grayscale mask")
            pixels = np.array(im)
        if not np.isin(pixels, [0, 255]).all():
            raise ValueError(f"{path}: use binary 0/255 masks, no feathering")
        item.mask = Mask.encode(pixels == 255)
    # Verify against the actual source file, not just its saved DB identity.
    with store.db.transaction() as conn:
        page = conn.execute(
            "SELECT p.*, s.slug FROM pages p JOIN series s ON s.id=p.series_id WHERE p.id=?",
            (page_id,),
        ).fetchone()
    if page is None:
        raise ValueError("unknown page")
    source = store.db.data_dir / "series" / page["slug"] / "source" / page["source_path"]
    source_pixels(source, annotation)
    return CropStore(store.db).submit(page_id, annotation, expected_revision=expected_revision)


def main() -> None:
    import argparse

    from ..config import settings

    parser = argparse.ArgumentParser(description="Local mask checkout, submit, approve and replay")
    parser.add_argument("action", choices=["replay", "checkout", "submit", "approve"])
    parser.add_argument("slug")
    parser.add_argument("--chapter", type=int, default=1)
    parser.add_argument("--page", type=int, required=True, help="Page birth number")
    parser.add_argument("--data-dir", type=Path, default=settings().resolved_data_dir())
    parser.add_argument("--edit-dir", type=Path)
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--actor")
    args = parser.parse_args()
    from ..cli import _owner

    with _owner(f"crop-{args.action}", args.data_dir):
        _run_command(args, parser)


def _run_command(args, parser) -> None:
    store = KomaStore.open(args.data_dir)
    try:
        page = next(
            (
                p
                for p in store.load_pages(args.slug, args.chapter)
                if p["birth_number"] == args.page
            ),
            None,
        )
        if page is None:
            parser.error("page not found; run koma ingest first")
        page_id = page["id"]
        if args.action in ("checkout", "submit") and args.edit_dir is None:
            parser.error("--edit-dir is required")
        if args.action in ("submit", "approve") and args.expected_revision is None:
            parser.error("--expected-revision is required")
        if args.action == "checkout":
            edit_checkout(store, page_id, args.edit_dir)
        elif args.action == "submit":
            submit_edit(store, page_id, args.edit_dir, args.expected_revision)
        elif args.action == "approve":
            CropStore(store.db).approve(
                page_id, expected_revision=args.expected_revision, actor=args.actor or ""
            )
        if args.action != "checkout":
            print(json.dumps(replay_page(store, args.slug, args.chapter, page_id), sort_keys=True))
    finally:
        store.db.close()
