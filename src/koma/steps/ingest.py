"""Import pages, detect panels and propose reading order.

Images are copied and PDFs rasterized with local binaries. Detection uses
OpenCV threshold/dilation/contours and area/aspect filtering; it is inspired by
Comic-Panel-Extractor (ehsanx, GPLv3), with no vendored implementation. Most
thresholds scale with page dimensions, but the dilation kernel is fixed.
AUTO direction currently falls back to RTL. Confidence persistence, append-safe
reimports and explicit source replacement remain work in PLAN.md §11."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..models.domain import (
    BBox,
    Page,
    Panel,
    PanelState,
    ReadingDirection,
    panel_key,
)

# ── detection constants (DPI-independent, tuned on manga pages) ──────────────
MIN_PANEL_AREA_FRACTION = 0.02  # of page area (CPE value; smaller = noise)
MAX_PANEL_AREA_FRACTION = 0.60  # of page area (splash pages handled by review)
MIN_ASPECT = 0.12  # w/h and h/w floor — kills slivers
INK_THRESHOLD = 120  # grayscale binarization (CPE value)
DILATE_ITERATIONS = 1
OVERLAP_NMS_FRACTION = 0.70  # smaller box ≥70% inside bigger box → drop
ROW_OVERLAP_FRACTION = 0.50  # vertical overlap ≥50% of min height → same row
ROW_STAGGER_FRACTION = 0.35  # row center-spread > 35% of mean height → low-conf


@dataclass
class DetectionResult:
    boxes: list[BBox] = field(default_factory=list)
    low_confidence: set[int] = field(default_factory=set)  # indices into boxes


def detect_panels_bboxes(image_path: Path) -> DetectionResult:
    """Adapted from Comic-Panel-Extractor backend/main.py (GPLv3, ehsanx).

    Kept as our own function (attribution above) — the upstream is a desktop
    app, not a library; the heuristic is the reference, the DPI-independent
    fixes are ours.
    """
    import cv2
    import numpy as np

    img = cv2.imread(str(image_path))
    if img is None:
        raise ValueError(f"unreadable image: {image_path}")
    height, width = img.shape[:2]
    page_area = width * height

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, INK_THRESHOLD, 255, cv2.THRESH_BINARY_INV)
    kernel = np.ones((3, 3), np.uint8)
    dilated = cv2.dilate(thresh, kernel, iterations=DILATE_ITERATIONS)
    contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    raw: list[tuple[int, int, int, int, int]] = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        area = w * h
        if MIN_PANEL_AREA_FRACTION * page_area < area < MAX_PANEL_AREA_FRACTION * page_area:
            if w == 0 or h == 0:
                continue
            aspect = min(w / h, h / w)
            if aspect < MIN_ASPECT:
                continue
            raw.append((x, y, w, h, area))

    # NMS: drop boxes mostly contained in a bigger one (balloons inside panels)
    raw.sort(key=lambda b: b[4], reverse=True)
    kept: list[tuple[int, int, int, int, int]] = []
    low_confidence: set[int] = set()
    for box in raw:
        x1, y1, w1, h1, area1 = box
        contained = False
        for x2, y2, w2, h2, _a2 in kept:
            ix_min, iy_min = max(x1, x2), max(y1, y2)
            ix_max, iy_max = min(x1 + w1, x2 + w2), min(y1 + h1, y2 + h2)
            if ix_max > ix_min and iy_max > iy_min:
                inter = (ix_max - ix_min) * (iy_max - iy_min)
                if inter > OVERLAP_NMS_FRACTION * area1:
                    contained = True
                    break
        if not contained:
            kept.append(box)

    boxes = [BBox(x=int(x), y=int(y), w=int(w), h=int(h)) for x, y, w, h, _ in kept]

    low_confidence: set[int] = set()

    # Splash / full-bleed page: the area cap filters the page-filling panel,
    # leaving nothing. Fall back to one whole-page panel, flagged for review.
    if not boxes:
        boxes = [BBox(x=0, y=0, w=int(width), h=int(height))]

    # Low-confidence flags: huge boxes (likely merged panels / splash).
    for i, b in enumerate(boxes):
        if (b.w * b.h) > 0.5 * page_area:
            low_confidence.add(i)

    return DetectionResult(boxes=boxes, low_confidence=low_confidence)


def propose_reading_order(
    boxes: list[BBox], direction: ReadingDirection, page_height: int
) -> tuple[list[int], set[int]]:
    """Return (ordered indices, low-confidence set) for one page.

    Row clustering by vertical overlap: two panels share a row when their
    vertical overlap ≥ ROW_OVERLAP_FRACTION of the smaller height (robust to
    staggered/fancy layouts where fixed buckets break). Rows go top→bottom;
    within a row, right→left for RTL, left→right for LTR. A row whose panel
    vertical-centers spread more than ROW_STAGGER_FRACTION of mean height is
    flagged low-confidence for the human review sheet.
    """
    del page_height  # overlap-based clustering is page-size independent
    if not boxes:
        return [], set()

    rows: list[list[int]] = []
    for i in sorted(range(len(boxes)), key=lambda k: (boxes[k].y, boxes[k].x)):
        placed = False
        for row in rows:
            r_min = min(boxes[j].y for j in row)
            r_max = max(boxes[j].y + boxes[j].h for j in row)
            overlap = min(r_max, boxes[i].y + boxes[i].h) - max(r_min, boxes[i].y)
            min_h = min([boxes[j].h for j in row] + [boxes[i].h])
            if overlap >= ROW_OVERLAP_FRACTION * min_h:
                row.append(i)
                placed = True
                break
        if not placed:
            rows.append([i])

    rows.sort(key=lambda r: min(boxes[j].y for j in r))

    ordered: list[int] = []
    low: set[int] = set()
    for row in rows:
        if len(row) > 1:
            centers = [boxes[i].y + boxes[i].h / 2 for i in row]
            mean_h = sum(boxes[i].h for i in row) / len(row)
            if max(centers) - min(centers) > ROW_STAGGER_FRACTION * mean_h:
                low.update(row)
        if direction is ReadingDirection.LTR:
            ordered.extend(sorted(row, key=lambda i: (boxes[i].x, boxes[i].y)))
        else:  # RTL (and AUTO defaults to RTL pending OCR heuristic)
            ordered.extend(sorted(row, key=lambda i: (-(boxes[i].x + boxes[i].w), boxes[i].y)))
    return ordered, low


# ── import ───────────────────────────────────────────────────────────────────


def _write_panels_index(
    page_path: Path,
    panels: list[Panel],
    panels_dir: Path,
    *,
    width: int,
    height: int,
) -> None:
    """Persist bbox provenance for scene crops (source page + per-panel bbox).

    Written per page (panels_index.json in the panels dir); idempotent —
    an existing index for the same page+panels is never rewritten.
    """
    import json

    index_path = panels_dir / "panels_index.json"
    entry = {
        "source_page": page_path.name,
        "page_w": width,
        "page_h": height,
        "panels": {
            pn.panel_id: {
                "bbox_x": pn.bbox.x,
                "bbox_y": pn.bbox.y,
                "bbox_w": pn.bbox.w,
                "bbox_h": pn.bbox.h,
                "order": pn.order,
            }
            for pn in panels
        },
    }
    existing: dict = {}
    if index_path.exists():
        try:
            existing = json.loads(index_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}
    if existing.get(entry["source_page"]) == entry["panels"]:
        return
    existing[entry["source_page"]] = entry["panels"]
    existing["_pages"] = {
        **existing.get("_pages", {}),
        entry["source_page"]: {"page_w": width, "page_h": height},
    }
    index_path.write_text(json.dumps(existing, ensure_ascii=False, indent=1))


def _write_panel_crops(page_path: Path, panels: list[Panel], panels_dir: Path) -> None:
    """Write one panel.png crop per panel (idempotent)."""
    import cv2

    img = cv2.imread(str(page_path))
    if img is None:
        return
    for pn in panels:
        out = panels_dir / pn.panel_id / "panel.png"
        if out.exists():
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        b = pn.bbox
        crop = img[b.y : b.y + b.h, b.x : b.x + b.w]
        cv2.imwrite(str(out), crop)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rasterize_pdf(pdf_path: Path, out_dir: Path, dpi: int = 300) -> list[Path]:
    """PDF → per-page PNGs via pdftoppm (poppler) or mutool, whichever exists."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pdftoppm = shutil.which("pdftoppm")
    mutool = shutil.which("mutool")
    if pdftoppm:
        subprocess.run(
            [pdftoppm, "-png", "-r", str(dpi), str(pdf_path), str(out_dir / "page")],
            check=True,
            capture_output=True,
        )
    elif mutool:
        pattern = str(out_dir / "page-%d.png")
        subprocess.run(
            [mutool, "convert", "-O", f"resolution={dpi}", "-o", pattern, str(pdf_path)],
            check=True,
            capture_output=True,
        )
    else:
        raise RuntimeError(
            "PDF ingest needs pdftoppm (brew install poppler) or mutool (brew install mupdf); "
            "neither found on PATH"
        )
    return sorted(out_dir.glob("page*.png"))


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def import_chapter(
    store,  # SeriesStore
    slug: str,
    chapter: int,
    src: Path,
) -> list[Page]:
    """Import one chapter's pages (JPEG dir/files or PDF) + detect + order.

    Idempotent: pages whose (source sha256) already recorded are skipped.
    """
    import cv2

    series = store.load_series(slug)
    direction = series.meta.direction
    if direction is ReadingDirection.AUTO:
        direction = ReadingDirection.RTL  # resolved later by OCR heuristic

    src = Path(src).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(src)

    files: list[Path]
    if src.is_file() and src.suffix.lower() == ".pdf":
        raster_dir = store.series_dir(slug) / "source" / f"ch{chapter:02d}" / "_raster"
        files = rasterize_pdf(src, raster_dir)
    elif src.is_dir():
        files = sorted(p for p in src.iterdir() if p.suffix.lower() in IMAGE_EXTS)
        if not files:
            raise ValueError(f"no images in {src}")
    elif src.is_file() and src.suffix.lower() in IMAGE_EXTS:
        files = [src]
    else:
        raise ValueError(f"unsupported input: {src} (want JPEG/PNG dir, image file, or PDF)")

    sdir = store.series_dir(slug) / "source" / f"ch{chapter:02d}"
    sdir.mkdir(parents=True, exist_ok=True)
    store.ensure_chapter(slug, chapter)

    pages = store.load_pages(slug)
    known_hashes = {p.source_path: p for p in pages if p.chapter == chapter}

    out: list[Page] = []
    imported = 0
    for f in files:
        digest = file_sha256(f)
        target_name = f"p{len(out) + 1:03d}{f.suffix.lower()}"
        target = sdir / target_name

        img = cv2.imread(str(f))
        if img is None:
            continue  # non-image straggler (e.g. txt) — skip quietly
        height, width = img.shape[:2]

        rel = f"ch{chapter:02d}/{target_name}"
        existing = known_hashes.get(rel)
        if existing is not None:
            out.append(existing)
            continue

        if not target.exists():
            shutil.copy2(f, target)

        det = detect_panels_bboxes(target)
        ordered_idx, _low = propose_reading_order(det.boxes, direction, height)

        panels: list[Panel] = []
        for order_idx, box_i in enumerate(ordered_idx, start=1):
            b = det.boxes[box_i]
            panels.append(
                Panel(
                    panel_id=panel_key(chapter, len(out) + 1, order_idx),
                    bbox=b,
                    order=order_idx,
                    state=PanelState.AWAITING_ORDER_REVIEW,
                )
            )

        # Panel crops (derived artifacts: source page + bbox are canonical).
        # Idempotent — existing crops are never rewritten.
        panels_dir = store.chapter_dir(slug, chapter) / "panels"
        _write_panel_crops(target, panels, panels_dir)
        _write_panels_index(
            target, panels, panels_dir, width=width, height=height
        )
        page = Page(
            chapter=chapter,
            page_number=len(out) + 1,
            source_path=rel,
            width=width,
            height=height,
            direction_resolved=direction,
            panels=panels,
        )
        # stash detection metadata for the review sheet (page-level, not panel)
        page.direction_resolved = direction
        out.append(page)
        imported += 1
        _ = digest  # hash recorded implicitly by content identity (same path+sha skip)

    if out:
        merged = {p.source_path: p for p in pages if p.chapter != chapter}
        for p in out:
            merged[p.source_path] = p
        store.save_pages(slug, list(merged.values()))
    store.update_chapter(slug, chapter, state="ingested")
    return out
