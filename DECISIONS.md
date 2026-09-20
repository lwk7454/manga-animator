# Current decisions — manga-animator

Updated 20 September 2026. These are owner decisions and retained product
requirements, not claims that features have been implemented or qualified.
[PLAN.md](PLAN.md) defines implementation contracts, defaults and acceptance gates.
A subsequent owner instruction takes precedence. Do not silently reopen settled
choices; update this file and the corresponding plan when a decision changes.

## Current generation direction — 20 September 2026

**D14 (revised): Direct panel R2V is the baseline; optional composition images are allowed.**
The owner reports the programmatic probe worked. Its measured quality, memory and
throughput remain unrecorded here. Preserve current runtime work. No mandatory
colorize/keyframe chain or fake KEYFRAMED state. A generated colored composition
may be used for selected shots if controlled tests show worthwhile improvement.

**D15: Colored video scenes may be reframed and expanded.**
Choose camera distance and generate surroundings appropriate to the target ratio.
Supporting movement is permitted; invented story events are prohibited. Preserve
source images and trace every shot to its source panels and story beats.

**D16: Human approval covers all characters and important objects/backgrounds.**
Official references are optional and still require approval. Generated candidates
require approval too. Store exact versioned bindings, variants and approval evidence.
An explicit draft preview remains allowed under D05.

**D17: The target product is a local web application.**
The UI manages references, approvals, shots, execution, review and exports. Core
operations must also be programmatic without manual ComfyUI graph editing. UI and
CLI share application services, durable state and one execution owner. Multiple
browser tabs do not authorize overlapping generation commands.

**D18: Both landscape 16:9 and portrait 9:16 are required.**
Compose/generate them independently and retain shared approved references. Several
panels may form one shot, and one panel may form several shots. Completion follows
reviewed story-beat coverage and ratio deliverables, not one video per panel.

**D19: Motion should avoid sluggish actions and slow camera pans.**
No comparison examples are available. Use concrete action direction and purposeful
camera movement; qualify the result visually at normal playback speed. Do not
assume interpolation or high FPS fixes pacing.

**D20: Target hardware is a local RTX 3090 with 32 GB system RAM.**
Measure actual GPU and host-memory use. H3 remains the primary implementation
default; alternate local models are bounded qualification candidates, not an
owner-mandated switch or an established quality ranking. No time SLA was given.

See [PLAN.md](PLAN.md), active sections A–J and milestones R0–R9. The historical
numbered plan remains subordinate where it conflicts. The no-web-app and strict
no-intermediate-image rules are superseded. No new implementation is claimed by
this planning update.

## Owner-confirmed requirements

| ID | Decision |
|---|---|
| D01 | Historical baseline only: no work existed at the original review. The owner has since run generation successfully. Preserve all current state and media; use nondestructive schema migrations. |
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
- Reimagine source-supported scenes as colored video under D14–D19. Preserve
  important story content and approved identities; pixel-identical panel composition
  is not required. Do not create portrait by cropping a finished landscape video.
- Japanese dialogue first, with English and Simplified Chinese subtitles.
  GPT-SoVITS is the first local TTS backend. Additional engines are deferred.
- Human order review and character/variant/voice review. Registry revisions and
  approval evidence are versioned; dependent outputs record exact bindings.
- Measured audio constrains dialogue/event coverage and frame-grid sizing. Never
  pad or change video speed to conceal short renders. Action shots may use reviewed
  trims of longer valid generations. Creative retiming is a proposed explicit,
  reviewed option disabled by default; preserve dialogue synchronization. Audio
  may receive silence padding.
- Local selected SFX/ambience/BGM assets; BGM resting gain 0.25 with speech ducking.
- One chapter video per configured ratio; explicit optional chapter merge.
  Stream-copy only compatible inputs; re-encoding must be explicitly selected.
- Declarative model profiles/components, explicit pins and qualification before
  production use. H3 R2V reference conditioning is in scope. Automatic previous-clip
  continuity and broad alternate-model adoption remain deferred.
- Dark local review UI, explicit pause/resume, no automatic idle detection or
  recurring background scheduler.

## Implementation defaults

PLAN.md active sections A–J select SQLite, a shared Python service layer, a local
React/TypeScript UI and one supervised execution worker, with inherited delivery
encoder/subtitle contracts where compatible. These are design choices,
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
  acceptance evidence; current measured evidence and subsequent owner reports control.
- Old #026 process-name killing/fail-open handoff and #027 strict no-offload
  guidance are replaced by D07–D09 and PLAN.md §§7–9.
- Old #021 broad multi-model marketplace/A-B scope is deferred. v1 requires
  explicit profiles/pins and bounded qualification, not a generic platform.

