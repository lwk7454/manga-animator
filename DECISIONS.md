# Current decisions — manga-animator

Updated 19 September 2026. These are owner decisions and retained product
requirements, not claims that features have been implemented or qualified.
[PLAN.md](PLAN.md) defines implementation contracts, defaults and acceptance gates.
A subsequent owner instruction takes precedence. Do not silently reopen settled
choices; update this file and the corresponding plan when a decision changes.

## Current generation direction — 19 September 2026

**D14: Direct manga-panel R2V replaces colorization and generated keyframes.**
The original panel is the video reference. Do not require a color master,
outpainted keyframe or fake KEYFRAMED state. This supersedes the retained
color-master/outpaint requirement below and the corresponding older PLAN stages.
The owner has now tested generated video and reports poor quality; D01 describes
the earlier baseline and is not permission to discard current runtime data.
The standalone R2V workflow remains pending owner-machine validation; see
[the test guide](docs/r2v-workflow.md). Other local-only, approval and recovery
requirements remain in force.

## Owner-confirmed requirements

| ID | Decision |
|---|---|
| D01 | The system has never run; no accepted generated work exists. No historical-output migration is required. Preserve unknown data rather than destroying it. |
| D02 | Imports append new pages. Unchanged reimports do nothing. Replacement/removal requires an explicit operation. |
| D03 | Order edits preserve panel IDs. Boundary editing comes later; new crop revisions invalidate dependent outputs. |
| D04 | Every configured ratio is required for aggregate completion. Each runs/retries independently; adding a ratio schedules only missing work. |
| D05 | Unapproved references require an explicit preview. Production output presented as reference-consistent requires approved bindings. |
| D06 | Reconcile uncertain submissions automatically first, then hold ambiguous attempts for review. Never blindly resubmit. |
| D07 | One active pipeline CLI command on the orchestrating system. Stopping a conflicting owner requires user approval. Status and targeted controls remain usable. |
| D08 | The GPU is shared. Support checkpointed pause, verified resource release and resume. Preserve completed work; unfinished inference may restart after an immediate stop. |
| D09 | Offloading is a performance preference, not an absolute ban. Prefer GPU execution and avoid excessive LLM CPU/RAM residency. Measure on the actual machine. |
| D10 | All generation stays local, including text, translations, image-bearing requests and previews. No cloud fallback. |
| D11 | Remove automated Civitai/WD scoring and cloud Llama Guard. Manual input/output review remains. `gate` may remain compatibility-only. |
| D12 | Missing required translations block completion. Retry missing substeps while preserving valid unchanged results. |
| D13 | Commit production files, maintained docs/config and long-term feature tests. No review-specific test dump, generated artifacts or scratch reports. |

## Retained product requirements

- Python/Typer orchestration on the Mac and local GPU services on the Windows
  RTX 3090 machine. Actual service installation/capability still needs verification.
- Animate the manga's approved panel composition. Shared color masters preserve
  source aspect ratio; each ratio gets full-source outpainting and independent
  motion generation, rather than cropping a completed video.
- Japanese dialogue first, with English and Simplified Chinese subtitles.
  GPT-SoVITS is the first local TTS backend. Additional engines are deferred.
- Human order review and character/variant/voice review. Registry revisions and
  approval evidence are versioned; dependent outputs record exact bindings.
- Measured audio determines frame-grid sizing. Never pad or change video speed
  to conceal short renders. Audio may receive silence padding.
- Local selected SFX/ambience/BGM assets; BGM resting gain 0.25 with speech ducking.
- One chapter video per configured ratio; explicit optional chapter merge.
  Stream-copy only compatible inputs; re-encoding must be explicitly selected.
- Declarative model profiles/components, explicit pins and qualification before
  production use. Experimental ref2va/continuity and alternate models are deferred.
- Dark local review UI, explicit pause/resume, no automatic idle detection or
  recurring background scheduler.

## Implementation defaults

PLAN.md §3.2 explicitly selects SQLite, one foreground owner, target delivery
resolution/encoder/subtitle behavior and other defaults. These are design choices,
not additional owner requirements or measured performance promises. Changes must
preserve the owner decisions above and be recorded in the plan.

## Superseded history

The earlier numbered log mixed decisions with unverified run reports and obsolete
cloud/scoring/offload requirements. Its original text is preserved in
[Git history](https://github.com/lwk7454/manga-animator/blob/01666663326be2b0bb2ac1d15fe27021e9e7b111/DECISIONS.md).
It is not current guidance. In particular:

- Old #013/#023 scoring/opt-in instructions are replaced by D11.
- Old #015/#017 cloud text allowances are replaced by D10.
- Old #022/#024/#025/#028–#030 run counts, timing and installation claims are not
  acceptance evidence; the owner's D01 clarification controls.
- Old #026 process-name killing/fail-open handoff and #027 strict no-offload
  guidance are replaced by D07–D09 and PLAN.md §§7–9.
- Old #021 broad multi-model marketplace/A-B scope is deferred. v1 requires
  explicit profiles/pins and bounded qualification, not a generic platform.
