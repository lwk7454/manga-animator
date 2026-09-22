"""CPU-only, source-pixel panel extraction. No model or network dependencies.

Masks use page-space, row-major foreground runs [offset, length]. IDs never
change when reading order changes. Proposals and final ownership are separate:
overlapping proposal masks are evidence, never an implicit z-order at export.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Mask(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    runs: list[tuple[int, int]] = Field(default_factory=list)

    def array(self, width: int, height: int) -> np.ndarray:
        flat = np.zeros(width * height, dtype=bool)
        end = 0
        for start, count in self.runs:
            if start < end or count <= 0 or start + count > flat.size:
                raise ValueError("mask runs must be positive, sorted, disjoint and on-page")
            flat[start : start + count] = True
            end = start + count
        return flat.reshape(height, width)

    @classmethod
    def encode(cls, mask: np.ndarray) -> Mask:
        flat = np.asarray(mask, dtype=bool).ravel()
        edges = np.flatnonzero(np.diff(np.r_[False, flat, False]))
        return cls(
            runs=[(int(a), int(b - a)) for a, b in zip(edges[::2], edges[1::2], strict=True)]
        )


class CropPanel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    mask: Mask
    needs_review: bool = True
    rationale: str = "Boundary requires human review"


class Component(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str
    kind: Literal["bubble", "artwork", "sfx"] = "bubble"
    mask: Mask
    owner_panel: str | None = None
    candidates: list[str] = Field(default_factory=list)
    needs_review: bool = True
    rationale: str = "Ownership requires tail/dialogue review"


class Annotation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    source_sha256: str
    width: int = Field(gt=0, le=32768)
    height: int = Field(gt=0, le=32768)
    direction: Literal["rtl", "ltr"] = "rtl"
    panels: list[CropPanel]
    components: list[Component] = Field(default_factory=list)
    reading_order: list[str]
    order_needs_review: bool = True
    order_rationale: str = "Heuristic row order; verify complex layouts"
    # The raw detections are kept even when their masks overlap or get refined.
    proposals: dict[str, Mask] = Field(default_factory=dict)
    dismissed: dict[str, str] = Field(default_factory=dict)
    detector: str = "manual"

    @model_validator(mode="after")
    def check_ids(self) -> Annotation:
        ids = [p.id for p in self.panels]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("panels must have unique IDs and cannot be empty")
        all_ids = ids + [c.id for c in self.components]
        if any(not reason.strip() for reason in self.dismissed.values()):
            raise ValueError("dismissed proposals require a rationale")
        if set(self.dismissed) & set(all_ids):
            raise ValueError("an object cannot be both active and dismissed")
        if len(set(all_ids)) != len(all_ids) or any(
            not re.fullmatch(r"[A-Za-z0-9_-]+", x) for x in all_ids
        ):
            raise ValueError("object IDs must be unique safe path components")
        if sorted(self.reading_order) != sorted(ids):
            raise ValueError("reading_order must contain every panel exactly once")
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            raise ValueError("source_sha256 must be a SHA-256 digest")
        if self.width * self.height > 100_000_000:
            raise ValueError("page exceeds mask allocation limit")
        for c in self.components:
            if c.owner_panel is not None and c.owner_panel not in ids:
                raise ValueError(f"unknown owner for {c.id}")
            if set(c.candidates) - set(ids):
                raise ValueError(f"unknown ownership candidate for {c.id}")
            if c.owner_panel is None and not c.needs_review:
                raise ValueError(f"unassigned component {c.id} must need review")
        return self

    @property
    def needs_review(self) -> bool:
        return (
            self.order_needs_review
            or any(p.needs_review for p in self.panels)
            or any(c.needs_review or c.owner_panel is None for c in self.components)
        )

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def ownership(self) -> dict[str, np.ndarray]:
        """Apply explicit component transfers; reject any unresolved pixel conflict."""
        masks = {p.id: p.mask.array(self.width, self.height) for p in self.panels}
        component_union = np.zeros((self.height, self.width), dtype=bool)
        assigned: dict[str, np.ndarray] = {}
        for c in self.components:
            m = c.mask.array(self.width, self.height)
            if not m.any():
                raise ValueError(f"empty component {c.id}")
            if np.any(component_union & m):
                raise ValueError("component masks overlap; refine them before export")
            component_union |= m
            if c.owner_panel is not None:
                assigned.setdefault(c.owner_panel, np.zeros_like(m))[:] |= m
        seen = np.zeros_like(component_union)
        for pid, mask in masks.items():
            mask &= ~component_union
            if pid in assigned:
                mask |= assigned[pid]
            if np.any(seen & mask):
                raise ValueError("panel ownership masks overlap; refine them before export")
            if not mask.any():
                raise ValueError(f"empty owned panel {pid}")
            seen |= mask
        for proposal in self.proposals.values():
            proposal.array(self.width, self.height)  # validate persisted evidence too
        return masks


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bounds(mask: np.ndarray) -> dict[str, int]:
    y, x = np.nonzero(mask)
    if not len(x):
        raise ValueError("empty mask")
    return {
        "x": int(x.min()),
        "y": int(y.min()),
        "w": int(x.max() - x.min() + 1),
        "h": int(y.max() - y.min() + 1),
    }


def png_bytes(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=False, compress_level=9)
    return out.getvalue()


def atomic_write(path: Path, data: bytes) -> None:
    """Idempotent bytes, atomic replacement; no stale output after annotation edits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == data:
        return
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def source_pixels(page: Path, annotation: Annotation) -> np.ndarray:
    data = page.read_bytes()
    if digest(data) != annotation.source_sha256:
        raise ValueError("source hash changed; explicit source replacement is required")
    with Image.open(io.BytesIO(data)) as im:
        if im.size != (annotation.width, annotation.height):
            raise ValueError("source dimensions do not match annotations")
        return np.array(im.convert("RGBA"))


def masked_image(pixels: np.ndarray, mask: np.ndarray, box: dict[str, int]) -> Image.Image:
    # Retained RGBA values are unchanged, including any source alpha.
    rgba = np.where(mask[..., None], pixels, 0).astype(np.uint8)
    x, y, w, h = (box[k] for k in ("x", "y", "w", "h"))
    return Image.fromarray(rgba[y : y + h, x : x + w])


def export_page(page: Path, annotation: Annotation, out: Path) -> dict:
    """Write immutable content-addressed masks/crops, then return index entries.

    Review-required exports are previews only. The SQLite gate, not an editable
    JSON flag, decides whether this revision can be used downstream.
    """
    pixels = source_pixels(page, annotation)
    masks = annotation.ownership()  # validate every mask BEFORE writing anything
    version = digest(annotation.canonical_bytes())
    root = out / "masks" / page.name / version
    entries = {}
    for order, pid in enumerate(annotation.reading_order, 1):
        mask = masks[pid]
        box = bounds(mask)
        mask_path = root / f"{pid}.mask.png"
        crop_path = root / f"{order:03d}_{pid}.png"
        atomic_write(mask_path, png_bytes(Image.fromarray(mask.astype(np.uint8) * 255)))
        atomic_write(crop_path, png_bytes(masked_image(pixels, mask, box)))
        entries[pid] = {
            **{f"bbox_{k}": v for k, v in box.items()},
            "order": order,
            "mask_path": str(mask_path.relative_to(out)),
            "crop_path": str(crop_path.relative_to(out)),
            "ownership_hash": digest(mask.tobytes()),
            "annotation_hash": version,
            "source_sha256": annotation.source_sha256,
            "needs_review": annotation.needs_review,
        }
    atomic_write(root / "annotations.json", annotation.canonical_bytes())
    return entries


def read_mask(path: Path, width: int, height: int, expected_hash: str) -> np.ndarray:
    with Image.open(path) as im:
        if im.mode != "L" or im.size != (width, height):
            raise ValueError("ownership mask must be a page-sized grayscale image")
        pixels = np.array(im)
    if not np.isin(pixels, [0, 255]).all():
        raise ValueError("ownership masks must be binary (0/255), not antialiased")
    mask = pixels == 255
    if digest(mask.tobytes()) != expected_hash:
        raise ValueError("ownership mask hash mismatch")
    return mask
