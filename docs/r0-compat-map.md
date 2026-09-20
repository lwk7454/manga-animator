# R0 compatibility map — existing contracts vs R0–R9 targets

Updated 20 September 2026 against main `6041230` (post cloud-agent merge `d63eb9f`).
This is the R0 exit artifact required by PLAN.md §I R0.

## Existing implementation inventory (verified against actual code)

| Area | State | Notes |
|---|---|---|
| Storage (`storage/database.py`) | schema v1, additive `CREATE IF NOT EXISTS`, refuses unknown nonempty legacy stores | 20 tables; version stamp only — R1 introduces real ordered migrations |
| Domain store (`storage/domain_store.py`) | transactional: series/chapters/pages/panels, import idempotency, work/attempt lifecycle, CAS, registry + approvals, artifacts, owner, controls | scope_key is generic TEXT — shot subjects fit without panel-column migration |
| Ownership (`scheduler/ownership.py`) | fcntl host lock + control requests + takeover | reused by web worker unchanged |
| Resource lanes (`gpu/lanes.py`, `gpu/resources.py`) | vision/TTS/Comfy lane admission, verified release | R5 web worker reuses |
| Script (`models/script.py`, `steps/script_store.py`) | typed line substeps, checkpoints | translations preserved for R7 |
| Registry (`steps/registry.py`) | candidate pairing, non-circular sheet approval, exact-reference bindings | kind = character/variant/voice — R1 extends to object/background |
| R2V probe (`r2v_probe.py`) | GPU-qualified on the 3090 (Sep 20): p015 single-char and p026 two-char ahegao scenes, turbo4, 124f | keep untouched as qualification entrypoint |
| Audio (`audio.py`, `mux.py`, `steps/audio_assembly.py`) | sample-resolved mixer, audio-first mux, dialogue timelines (panel slots) | R7 moves to shot slots |
| Video (`video.py`) | ffprobe validation, encoder policy, exact assembly manifests | reused |
| Web/API | **absent** | R2 builds FastAPI + static serving from scratch |

## Panel-only constraints blocking shots (verified)

1. `panel_bindings` requires `panels(id)` FK — shot occurrence bindings need a
   new table (`shot_bindings`) rather than coercion. Additive.
2. `work_items.scope_key` comment says "series/chapter/page/panel/line/segment"
   — comment only; no CHECK constraint blocks shot scope keys. No migration needed
   for work items; validation lives in typed payloads.
3. `panels` state enum gates (`SCRIPTED…KEYFRAMED`) live in `models/domain.py`
   (`PanelState`) and store methods — the plan explicitly forbids relabeling shots
   as KEYFRAMED; shot coverage is validated in the new shot store, not panel states.
4. No approval-evidence table for object/background kinds — R1 adds
   `asset_approvals` and widens `registry_revisions.kind` via additive migration.

## Migration plan for R1 (schema v2, additive only)

New tables (no column changes to existing tables):
- `assets` (series-scoped stable id, kind character/object/background, importance,
  active approved revision pointer)
- `asset_revisions` (immutable payload: palette, identity features, variants,
  source citations, origin, artifact ids, parent revision)
- `asset_approvals` (exact revision, reviewer action, timestamp, state
  draft/awaiting_review/approved/rejected/superseded)
- `asset_occurrence_bindings` (panel OR shot occurrence → exact asset revision)
- `story_beats` (chapter plan revision, ordered beats, dialogue ids, causality)
- `shot_plan_revisions` (shot id + immutable revision, beat coverage, camera,
  route direct_r2v | composition_assisted)
- `shot_ratio_specs` (per-ratio canvas, placement, reference manifest, fingerprint)
- `shot_candidates` (immutable outputs per shot/ratio, acceptance, edit decisions)
- `run_plans` (frozen chapter/shot revisions, ratios, pins, reused-work map)

Backfill: existing `registry_revisions` character approvals map to new
`assets`/`asset_revisions` rows with `origin='registry-v1'` — approval evidence
preserved (approved_review_id carried over), no invented approvals.

## Reused unchanged

Ownership/fencing, artifact staging→promotion, attempt reconciliation (uncertain
submissions hold, never blind resubmit), resource-lane verified release, mixer/
mux contracts, ffprobe validation, R2V probe evidence files.