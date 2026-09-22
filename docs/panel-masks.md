# Local panel masks and deterministic crops

This crop-only stage copies decoded source pixels. It does not generate or
retouch artwork, call a model, upload a page, download weights, or require a GPU.
SQLite schema v5 adds one table to v4; existing tables and data are retained.

## What is automatic, and what needs review

`koma ingest` still uses the existing contour detector to identify panel
proposals and propose RTL/LTR order. It now fills matching contours into masks,
retains raw proposals, and proposes enclosed light regions as **unclassified
balloon candidates**. Small overlapping panel proposals provisionally take
precedence. Every initial boundary, component assignment and order proposal
requires review. These are not reliable automatic hair/bubble segmentations.

A human can refine page-space masks and assign a bubble, its tail, artwork or
SFX to any panel, including a panel whose rectangle is elsewhere on the page.
The exporter removes that component from all other owners before assigning it.
Unknown ownership stays flagged and is transparent in preview exports. Overlapping
final ownership or component masks, empty panels, invalid IDs, bad dimensions,
source hash changes and malformed runs fail closed. No invented inpainting fills
the hole left by a transferred component.

There is no SAM, Magi, Manga-Segment, or VLM integration in this crop-only change.
No third-party model code or weights are bundled. The existing OpenCV fallback
and its attribution remain; no new runtime dependency is added. Human-reviewed
masks, not the initial proposal quality, provide the pixel ownership guarantee.

## Authority and output layout

`crop_annotations` holds immutable revision payloads and references ordinary
`reviews` records with `kind=panel_crops`. Submission uses an expected revision;
approval is a separate named-human action. The latest revision must be approved.
Older approvals cannot unblock a newer pending revision. Derived JSON edits never
silently alter canonical state.

The versioned JSON schema records source SHA-256/dimensions, stable object IDs,
raw proposals, panel masks, component masks/candidates/owners/rationales,
per-item review flags, direction and a complete reading-order permutation.
Masks use foreground runs `[offset, length]` in flattened row-major page space.
False-positive objects may be removed only with an entry in `dismissed` explaining
why; raw proposal evidence is retained. Object IDs never change when reordered.

Alongside the existing rectangular `<panel_id>/panel.png` files:

```text
panels/panels_index.json
panels/masks/<source-filename>/<annotation-sha256>/annotations.json
panels/masks/<source-filename>/<annotation-sha256>/<panel_id>.mask.png
panels/masks/<source-filename>/<annotation-sha256>/001_<panel_id>.png
```

`panels_index.json` preserves the old page-to-panel mapping and `_pages` dimensions.
Schema 2 adds `_annotations` with revision, approval status and annotation path;
each panel adds mask/crop paths, source and ownership hashes, review status and
the tight bbox of its owned pixels. Old revisions and images are retained.
`Panel.bbox` is now a derived bounding canvas, not complete geometry; the mask is
canonical. `Panel` exposes derived mask/crop paths and review metadata for old readers.

Approved RGBA crops are registered as `kind=panel` artifacts. Shared image selection
uses them for existing panel consumers, while old rectangle-only pages fall back
to their existing images. Crop revision hashes fence old panel artifact selection
and frozen panel references. No image-upload transport changes are needed: it
already uploads the referenced PNG file bytes without RGB conversion.

## Local editing and replay

Import through the existing command, then check out the page's current revision:

```bash
KOMA_SKIP_OWNER=1 koma ingest example /path/to/pages --chapter 1
python -m koma.steps.ingest checkout example --chapter 1 --page 1 \
  --edit-dir /tmp/page-1-masks
```

Use `--data-dir /path/to/data` with either command when needed. Normal commands
use the existing command-owner lock. `KOMA_SKIP_OWNER=1` is only for the established
single-process test/maintenance environment.

Checkout writes `annotations.json`, `revision.txt`, and one page-sized grayscale
PNG per active panel/component. Edit these PNGs in a local image editor using
only values 0 and 255, with no resizing or feathering. The panel masks specify
base geometry; component masks transfer their pixels to `owner_panel` at export.
Inspect tails and dialogue to assign owners in JSON; record the rationale.
Update `reading_order` as needed. Clear each `needs_review` and
`order_needs_review` only after reviewing that decision. Do not edit source hash,
dimensions or existing panel IDs. New components require a unique ID and a mask
PNG named after that ID. Keep false-positive dismissal reasons in `dismissed`.

Submit with the revision from `revision.txt`, inspect the resulting preview PNGs,
then approve the revision number reported by submission:

```bash
python -m koma.steps.ingest submit example --chapter 1 --page 1 \
  --edit-dir /tmp/page-1-masks --expected-revision 1
python -m koma.steps.ingest approve example --chapter 1 --page 1 \
  --expected-revision 2 --actor Wayne
python -m koma.steps.ingest replay example --chapter 1 --page 1
```

Unchanged submission does not create a new revision. Re-running ingest or `replay`
uses SQLite annotations, never new detections or semantic guesses. Repeated
exports are byte-identical with the same Pillow/zlib runtime; cross-version PNG
encoder identity is not promised. Retained decoded RGBA values are exact; pixels
outside the owned mask are all zero. Transparent source pixels retain their source
RGBA values if owned. Replay restores missing/tampered derived exports.

The existing review contact sheet highlights crop review needs. Order certification
and lifecycle advancement cannot bypass outstanding crop review. Certifying a
different order after mask approval creates and explicitly approves a new order
revision; panel identities remain unchanged. Shot readiness also blocks pending
crop revisions. Exports marked pending are previews, never validated panel artifacts.

## Scene callers

For a mask-enabled index, pass the index directory explicitly:

```python
scene_crop(source_page, panel_ids, index[source_page.name], output_png,
           masks_root=panels_dir)
```

This composites the union of member masks using source pixels, preserving holes,
overfill and transparency. Every member must have geometry. Mixed mask/rectangle
groups, missing or modified masks, cross-page sources and overlapping member masks
are errors, never a fallback to a rectangular union. Bbox-only legacy groups keep
their previous behavior.

## Validation and limits

Tests use synthetic pages only: displaced bubble/tail transfer, fine overfill,
source alpha, disjoint masks, byte replay, scene holes, corrupt masks, stale edits,
explicit approval, artifact registration and SQLite-only replay authority.
The existing suite's hardcoded `/tmp/koma-qual` fixture was made hermetic.

No real-page quality benchmark or GPU evaluation was performed. Semantic ownership
and fine boundaries still need human correction. There is no browser mask painter;
the editable PNG checkout is the local editing interface. Existing panel creation/
deletion and source-replacement workflows remain separate from this feature.
