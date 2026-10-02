# Local panel masks and deterministic crops

This crop-only stage copies decoded source pixels. The existing local VLM makes
semantic ownership and reading-order decisions; it never draws export pixels or
generates contours. No cloud service or downloaded weights are used. SQLite
schema v6 adds annotation and durable job tables; existing data is retained.

## Process and review a chapter

```bash
KOMA_VISION_BASE_URL=http://192.168.50.103:11434 \
  koma crop-chapter example /path/to/pages --chapter 1
```

Use the existing `vision.model` configuration for the loaded model. Omit
the source folder to process an already imported chapter; `--queue-only` saves
work without inference. Open `/crops` in the existing web app to inspect original
pages and numbered transparent crops, correct order/owners, approve inspected
pages, or select panels and request rework with instructions. Rework looks for
additional boundary candidates around selected panels and re-adjudicates related
objects. Donor/recipient panels affected by ownership transfers require review.
Pixel masks remain editable through the checkout workflow below.

The VLM receives full-page context with panel IDs and native-resolution object
crops in small batches. Strict JSON must account for every supplied object and
use existing panel IDs; rationale and confidence flags are persisted. Uncertain
boundaries always require human inspection, even when the model is confident.
Only private/loopback model endpoints are accepted by the existing client.
Both server-root URLs and URLs ending in `/v1` (with or without a trailing slash)
are accepted. Reading order uses one labeled page image and a concrete provisional
answer; ownership also uses concrete object IDs and JSON types. An unchanged
echo remains review-required rather than masquerading as a verified decision.

Jobs run sequentially under the application owner lock. Failures remain visible
and block approval-dependent consumers; other pages continue. Resume queued work
after a restart. Committed results replay without another VLM call; stale rework
cannot overwrite manual revisions. Select failed panels and request rework to
retry. Model calls run outside SQLite transactions. Unload ComfyUI before using
the shared GPU; this stage does not manage GPU services or load a second model.

Retry a failed request after fixing the endpoint/model configuration:

```bash
koma crop-retry PAGE_ID
```

Find `PAGE_ID` in `panels_index.json` under `_annotations` or the chapter review
API. This preserves failed-job history, retries its selected panels/instructions,
and rejects stale requests after manual edits. `--queue-only` defers inference.
Failures are never retried indefinitely in the background.

Existing ink-only proposals are persisted and ordinary ingest/replay intentionally
does not replace them. To explicitly replace a page's base masks with the new
closed-region proposals, then re-run full-page semantics:

```bash
koma crop-retry PAGE_ID --rebuild-regions
```

This **replaces current base-mask edits** using the original detector extents;
previous revisions and proposals remain available. Bubble assignments are retained
as provisional, old ink refinements are dismissed with a rationale and rediscovered,
and the new revision requires review. The command also works for previously
completed/approved pages. Do not use it when intending only to replay approved masks.

## What is automatic, and what needs review

`koma ingest` still uses the existing contour detector to identify panel
proposals and propose RTL/LTR order. Base geometry now closes small border gaps
with a 5×5 mask operation and floods from the exterior to retain enclosed interiors,
including white, light-colored and screentone pixels. A credible filled region
must cover at least 40% of its detector extent and contain at least 10% enclosed
interior (or be at least 65% dark). These are provisional heuristic thresholds.
When an open border fails that check, its full extent is retained with an explicit
boundary warning, rather than treating disconnected ink as a panel. Overlapping
extents still have disjoint provisional ownership and require correction.
The stage retains raw proposals and proposes enclosed light regions as **unclassified
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

There is no SAM, Magi, or Manga-Segment dependency in this change.
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

```bash
uv pip install --python .venv/bin/python -e '.[dev]'
KOMA_SKIP_OWNER=1 .venv/bin/python -m pytest
make lint VENV=.venv
```

Pytest explicitly includes `src` for fresh-checkout collection; install dependencies
and the package as above for CLI/subprocess tests. The web worker intentionally
owns a separate thread-local SQLite connection; WAL and short transactions allow
API reads/edits while the application owner lock serializes workers.

Tests use synthetic pages only: displaced bubble/tail transfer, fine overfill,
source alpha, disjoint masks, byte replay, scene holes, corrupt masks, stale edits,
explicit approval, artifact registration and SQLite-only replay authority.
The existing suite's hardcoded `/tmp/koma-qual` fixture was made hermetic.

The local reviewer reported successful byte-level fidelity/replay and ownership
rework on a real page, but found prompt echo and missing interiors in the earlier
revision. Those have synthetic regression coverage; the revised code still needs
a local recheck because the private GPU host is unreachable from this environment.
Before production use,
run a chapter on that host, inspect difficult overlapping layouts and check local
VLM latency/JSON behavior. Synthetic tests cover model replies, durable failures,
restart recovery, stale edits, and web approval/rework. Connected ink and light
regions are imperfect proposals; fine hair and intertwined silhouettes may need
manual mask correction. There is no browser mask painter;
the editable PNG checkout is the local editing interface. Existing panel creation/
deletion and source-replacement workflows remain separate from this feature.

For the local recheck, rebuild the affected page explicitly, run all ownership
batches (default four objects), review and replay it, then record per-panel owned
area/extent, missing-interior defects, owner/order changes, mask-edit count and
review time. Inspect the source and scene union side by side: area coverage alone
cannot certify correct ownership. The real-page correction-volume benchmark and
SAM/Manga-Segment/Magi evaluation remain outstanding; no automatic quality claim
is made for fine boundaries.
