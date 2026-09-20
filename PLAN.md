# Koma — colored manga-to-video and local web app implementation plan

**Current review handoff: 20 September 2026. Implementation status is recorded in the checkpoint below; the remaining sections specify the target design.**
**Inspected main:** `6be159d744ee11b22d4b21f01be3bb55c57b5e78` (original planning baseline: `0aa266efabc717b450e1ee5930a2170eba0613b2`).

This section, R0–R9, is the active implementation sequence. It supersedes conflicting requirements in the retained historical plan below. Preserve compatible reliability, audio, import and storage contracts; do not repeat completed work. The owner reports that the programmatic R2V test worked. That establishes an owner-reported successful run, not measured visual quality, memory, throughput or full application qualification. Preserve current generated work and state.

## Review checkpoint — 20 September 2026 (through upstream `6be159d`)

This checkpoint supersedes session-report claims that R0–R8 are finished or only GPU qualification remains. Code and tests exist for parts of each milestone, but there is no complete colored/reframed production workflow. The fixes accompanying this checkpoint do not implement the missing creative stages.

| Milestone | Verified status and remaining work |
|---|---|
| R0 | Baseline/schema compatibility inventory exists. The review covered every changed production module from R0 onward, including the later R7/R8 commits. |
| R1 | Asset/shot stores exist. Review fixes atomic migrations/future-version rejection, revision allocation, transactional approval CAS, binding ownership, exact-reference production gates and coverage errors. Still missing full immutable run-plan/dependency orchestration, explicit unresolved-binding resolution and comprehensive legacy-registry import. |
| R2 | Local approval API and a small console exist, now with contained artifact previews and exact host/origin validation. This is not the planned complete reference-management app: proposal/upload workflows, identity merging, variant review and storyboard editing remain. |
| R3 | Reference manifest/compiler helpers and monochrome smoke tests exist. Review fixes flat ComfyUI dynamic socket wiring, missing uploads, source hash/tag checks and upload identity. Approved colored sheet generation/binding and color-consistency qualification remain. |
| R4 | Deterministic beat/shot proposal helpers exist. Review preserves typed OCR line IDs, reports unknown beats without crashing and rejects duplicate dialogue coverage. The known-beat validator is structural, not a semantic guarantee against invented events. No scene reconstruction, local shot-direction model, composition generation or composition-assisted adapter is implemented. |
| R5 | Injected-transport worker and run API components exist. Review adds same-attempt resume, conservative recovery of submitting/uncertain states, exact request/server reconciliation, single-job adoption under ownership, serial local dispatch, durable status fallback and transactional candidate registration. `koma serve` deliberately returns 503 for generation until an actual production transport is supplied. Raw probe tests do not qualify this integration. Shot-plan→compiled work creation, production transport, durable command idempotency, pause/resource controls and frozen-plan orchestration remain. |
| R6 | Candidate store/review helpers exist. Review fixes exclusive acceptance across old accepted siblings, CAS, idempotent registration, ratio-specific budgets and unresolved-attempt retry gating. Default budget is two candidates, matching the plan rather than the previous undocumented four. The UI review matrix, measured quality decisions, explicit retry-work creation, trim/retime manifests and qualified motion outcomes remain. |
| R7 | Per-shot assembly primitives, SRT and an explicit preview API exist. Review fixes local-only TTS, missing dialogue/translation handling, speech truncation, source ordering/hash/path checks, per-ratio immutable output directories, shared execution ownership and durable preview status. Production assembly is unavailable until approved script/voice bindings replace arbitrary request text/reference paths. Chapter assembly, dialogue across cuts, delivery-ratio transforms/upscale, embedded/burned subtitles, independent production ratio completion and promotion manifests remain. **R7 is not trivial to complete and is not marked complete.** |
| R8 | A minimal static console is served and packaged. Review fixes broken default-worker wiring, asset names, unsafe DOM insertion, one-ratio controls and silent empty-dialogue requests. Full workflow UI and production end-to-end qualification remain. |
| R9 | Release blocked on the above features and acceptance gates. Do not delete the functioning probe/control workflow or owner media. |

### Correctness changes and compatibility notes

- Schema version 4 adds a candidate review revision. All migrations now execute within one SQLite transaction; unsupported newer schemas are refused. Back up owner data before upgrading. No migration invents asset approvals or discards media.
- The R2V submitter injection now receives `(graph, upload_plan, context)`; an adapter must persist/send `context.request_identity` in remote metadata. Reconciliation snapshots must contain `graph`, `request_identity` and `server_identity`. Graph equality alone cannot identify an attempt. Legacy uncertain requests without this evidence stay held for review.
- Deadlines and poll/download/validation transport errors preserve accepted nonterminal attempts. Resume polls the original prompt. The known old `interrupted` deadline record can recover its recorded prompt under ownership; it never creates a replacement submission. New work is held behind an outstanding local attempt.
- A valid artifact is registered as a pending motion candidate in the same transaction as promotion. Technical generation success remains distinct from human acceptance and final chapter completion.
- Assembly API requires `preview: true`; its success includes `production_complete: false`. Required subtitle languages come from series settings. Missing speech/translation or speech longer than video fails visibly. Silent video is allowed only as a raw generation artifact or an explicitly silent preview, never evidence of finished dialogue delivery.
- Preview files live under separate shot/ratio/request directories. Subtitle requests require the ratio. Preview operation status is persisted in SQLite, survives API restart, and is marked interrupted when a worker has disappeared. Preview regeneration is explicit; this does not claim full production assembly checkpointing.
- The lightweight console remains vanilla JavaScript rather than the earlier React default. Do not rewrite it solely to match a framework choice; first implement the missing user flows through shared services.

### Evidence boundaries and next implementation order

The owner supplied `qual_e2e_assembly.mp4` plus two PNG contact sheets. Direct inspection confirms monochrome sampled outputs; FFprobe confirms the MP4 has 124 H.264 frames at 24 fps, 864×480, and a stereo AAC track. No portrait MP4 was supplied in that attachment set, so its audio absence is owner-reported. Contact sheets cannot prove natural-speed motion or absence of temporal flicker. The 864×480 and 480×864 canvases are not exact delivery ratios. See the corrected qualification scope in [the hardware notes](docs/qual-hardware-2026-09-20.md).

Next: finish colored asset proposal/approval and ratio-specific composition generation; compare the explicitly distinct R2V-reference and I2V-first-frame routes on approved references; integrate the actual worker transport and frozen plan/work creation; finish production R7 audio/subtitle/chapter assembly for both ratios; then complete the workflow UI and the three-case/two-seed acceptance matrix. Do not use a prompt that preserves B&W manga as evidence that the colored-video requirement is met. Preserve that original prompt only as a named technical control.


## A. Settled direction and explicit supersessions

| Area | Current contract |
|---|---|
| Output | Colored scenes reimagined for video, with suitable camera distance and generated surroundings filling the requested canvas. |
| Story | Supporting movement is allowed; invented story events are prohibited. Preserve identity, intent, causality and important objects. |
| References | Optional official references; human approval is required for every character and for important objects and backgrounds. Generated proposals also require approval. Official provenance is not approval. |
| Product | A local web app manages the workflow. All core operations must also be accessible programmatically; no manual ComfyUI editor steps. |
| Video route | Direct panel R2V remains the starting route. An optional generated color composition is permitted if controlled testing demonstrates useful quality improvement. It is not a mandatory per-panel stage. |
| Ratios | Both 16:9 and 9:16, independently composed and generated. Shared references and story facts; separate ratio-specific output work. |
| Motion | Natural-speed body movement and purposeful camera motion; avoid sluggish actions and slow drifting pans. No example clips are available. |
| Granularity | Several panels may become one shot; one panel may become several shots. Preserve source-to-shot and story-beat traceability. |
| Hardware | One local RTX 3090, 32 GB system RAM. Discover actual GPU memory and available RAM before profiling; do not mistake system RAM for VRAM. |
| Backend | Keep MiniMax H3 primary. Alternative backends are optional qualification candidates, not a required platform rewrite or a claim of better quality. |
| Locality/recovery | Retain local-only generation, independent retries, durable uncertainty reconciliation, single execution owner, approved takeover, pause/release/resume. |

Supersede the earlier ban on intermediate images, the ban on a web application, one-panel/one-video assumptions, and mandatory pixel-preserving full-source outpainting. Recomposition may redraw a view; source images remain immutable. The old instruction that no real runs exist is historical. The old colorize→keyframe→render chain is not the default.

Keep the prohibition on changing speed merely to conceal short generation or broken audio timing. Proposed creative pacing defaults: trim unused lead-in/tail; prefer regeneration over retiming; any creative speed adjustment is explicit, reviewed and recorded, disabled by default. Do not automatically stretch speech or a finished dialogue mix.

Design defaults, distinguished from owner requirements: polished 2D anime appearance; adjacent-panel/page context; stable camera unless movement has narrative purpose; hard cuts; one-time reusable asset approval plus per-clip acceptance. Style is project-configurable. Auto-proposed shots remain editable before dispatch. These choices do not promise model performance.

## B. Architecture and existing code to reuse

The web app and CLI call the same Python application services. SQLite and immutable media remain authoritative. Keep ComfyUI as a private generation service; the browser never sends workflow graphs directly to it. Do not implement a second scheduler in the frontend or reuse probe JSON files as the production database.

| Existing seam | Planned change |
|---|---|
| `storage/database.py`, `storage/domain_store.py`, `storage/artifacts.py` | Add versioned assets, shot plans, bindings and edit decisions through migrations; retain transactional work/attempt/artifact promotion. |
| `models/domain.py`, `models/script.py`, `models/work.py` | Add typed asset/shot payloads without treating panel progress enums as completion authority. |
| `application/control.py`, `scheduler/ownership.py`, `gpu/lanes.py` | Extend current run controls to web requests and a supervised single worker; reuse owner fencing and measured release. |
| `steps/registry.py`, `steps/cards.py`, `review/sheets.py` | Reuse registry and approval evidence; expose them through shared services and the new UI. |
| `adapters/compiler.py`, `adapters/registry.py`, `gpu/worker.py` | Add distinct direct-R2V and optional composition-assisted roles, installed-schema checks and pinned multi-reference bindings. |
| `r2v_probe.py`, `config/workflows/minimax_h3_panel_r2v_*.api.json` | Keep working qualification entrypoints. Extract shared transport/compiler helpers only where duplication would change behavior; retain recovery contracts and existing probe commands. |
| `audio.py`, `video.py`, `encode.py`, `mux.py`, `steps/audio_assembly.py` | Adapt timelines and assembly from panel slots to shot slots, preserving exact dialogue/subtitle mapping and technical validation. |
| New `application/assets.py`, `application/shots.py`, `application/run.py` | Own domain operations, immutable planning and orchestration. Use these names unless existing newer equivalents already serve the purpose. |
| New `web/` frontend and `src/koma/web/` API | React/TypeScript/Vite UI and a small FastAPI service are the selected implementation defaults. Serve built static assets and API from one local origin. No Next.js server, second database, Redis or distributed worker system. |

Only adopt dependencies when implementing the relevant milestone; pin them in existing project dependency mechanisms and a frontend lockfile. Keep Python as the orchestration runtime; do not rewrite working services into TypeScript. No runtime CDN, remote fonts, analytics or cloud image processing. A development frontend proxy must preserve the production API contract.

Suggested launch contract: `koma serve` starts the local API/static UI; a single supervised worker process acquires the existing execution lock only while executing work. Read-only pages and approval edits remain available during generation. Starting a second server/worker must not bypass the ownership policy. The server may remain alive while the GPU is released. This replaces the old foreground-CLI-only default without creating multiple execution owners.

## C. Durable domain and invalidation contracts

Implement migrations against the actual current schema, not a second database. Exact SQL names may follow existing conventions; these logical entities and constraints are required:

| Entity | Required content and invariants |
|---|---|
| Asset | Stable series-scoped ID, kind `character/object/background`, display name, importance, source occurrences and active approved revision pointer. All characters require approval; objects/backgrounds require it when marked important. |
| Asset revision | Immutable revision ID; color palette, identity features, geometry/scale, variant/state, source citations, uploaded/generated origin, image artifact IDs/hashes, generation manifest, parent revision. Separate identity from shot lighting. |
| Asset approval | Exact revision/hash, approving user action, timestamp and decision/reason. State `draft/awaiting_review/approved/rejected/superseded`. Only explicit user action approves; candidate generation never does. |
| Asset occurrence/binding | Panel or shot occurrence mapped to exact asset and variant revision. Explicit unknown/ambiguous state blocks production. Do not infer exact bindings from matching display names. |
| Story beat | Stable ID within a revisioned chapter plan; source panel/crop revisions and supporting text; action, participants, dialogue IDs, causal ordering and allowed supporting motion. |
| Shot plan revision | Stable shot ID plus immutable revision, ordered beat coverage, source panel revisions, approved asset bindings, shot purpose, start/end state, action beats, camera/framing, duration policy and route. |
| Ratio shot specification | Shot revision, ratio, target canvas, subject placement/scale, safe areas, framing rationale, reference bundle, route, generation profile and source dependency fingerprint. |
| Candidate/acceptance | Multiple immutable outputs per shot/ratio; selected artifact and explicit acceptance tied to current dependency revisions. Keep rejected candidates and reasons until explicit cleanup. |
| Edit decision | Candidate ID, source frame in/out, playback rate default 1, output frame count, audio/line mapping and reviewed reason. Immutable revision, not destructive file editing. |
| Run plan | Frozen chapter/order/shot revisions, required ratios, profile pins, dependencies and work IDs. Replanning creates a new revision and reuses matching completed work. |

Extend existing generic work/artifact records to support shot subjects, including foreign keys and uniqueness. Preserve existing panel work; do not coerce a shot into a fake panel or renumber panel IDs. If panel columns are currently mandatory, migrate safely to an explicit subject kind with validation. Back up before migration, test rollback/failure recovery, and refuse unsupported versions. No destructive reset justified by the original no-run decision.

A chapter's approved beat coverage is the completion authority: every required beat must be covered, dialogue lines must have one intended delivery occurrence, and intentional repeated views must be labelled. Multiple shots of one panel are not duplicate import errors. A grouped shot must preserve beat order and may not skip required events. Panel-level progress becomes a derived view of its required shot contributions for every ratio.

Fingerprint generation work from source crop/context hashes, beat/shot revision, exact asset bindings, ratio/framing, prompt/compiler version, profile/component pins, seed and conditioning artifacts. Timing changes invalidate renders only when conditioning or required frame count changes; subtitle-only edits should not invalidate silent raw video unnecessarily. Assembly fingerprints also include selected clips, trims, timeline, audio and subtitle revisions.

| Change | Required effect |
|---|---|
| Approve a new character/object/background revision | Show affected bound shots; rebind only through an explicit action. Historical renders retain old bindings. Old-approved outputs remain valid for their frozen run unless that approval is revoked. |
| Revoke approval or explicitly rebind shots | Mark affected current deliverables stale; block promotion under revoked bindings. In-flight results may be archived but cannot become current. |
| Edit one ratio's framing/composition | Invalidate only that ratio's dependent generation/edit/assembly. |
| Reorder panels or regroup shots | New chapter/shot plan; reuse byte-identical matching dependencies, revalidate beat and dialogue coverage. |
| Change a crop revision | Invalidate dependent context, bindings/proposals where applicable and shot outputs; preserve panel identity. |
| Add a ratio | Schedule missing ratio specifications and outputs; retain references and other completed ratios. |
| Reject a clip | Keep its evidence, create an explicit new candidate attempt when requested; do not silently invalidate another ratio. |

At dispatch and promotion, recheck required approvals, work fingerprint, run revision and owner fence in short transactions. Never hold database transactions during model calls. A concurrent UI edit must not allow stale output to replace a newly accepted result.

## D. Reference preparation and local approval UI

Reference preparation precedes production-consistent generation, but candidate reference generation must not depend on its own approval. Use a separate proposal role and namespace. Explicit previews may use drafts and must be visibly labelled; they cannot be silently promoted to production.

1. Import chapter and review order; run local extraction to propose characters, important objects, locations and occurrences. Reuse existing script substeps and preserve successful translations.
2. UI shows the inventory with source panel crops, uncertain identity links and importance flags. The user can merge/split identities and mark recurring/story-relevant objects or locations important. Unresolved important entities block production. Incidental background decoration does not require an individual asset record.
3. Allow official reference upload or generate a local colored proposal from source crops and a style description. Preserve original images and origin notes. Optional official references constrain the proposal but are never automatically approved.
4. Character approval view presents a face crop, full-body view where supportable, palette and outfit/state variants. Additional angles are useful candidates, not fabricated canonical facts. Object view includes silhouette, palette and scale; background view includes layout/landmarks, palette and continuity notes.
5. Approve/reject/regenerate/edit palette through revisioned actions. A human must inspect every required asset. Batch approval is allowed only for explicitly selected visible revisions, never unseen proposals.
6. Bind approved variants to occurrences. Conflicting official references or unclear outfits remain unresolved until reviewed. Return-to-review links must identify which exact asset blocks a shot.

The UI needs: project/chapter list; source/order view; asset inventory/review; shot storyboard with per-ratio composition; run queue and resource status; clip comparison/acceptance; export status. Deliver the asset screen first, not a large visual workflow editor.

Media is served by artifact ID with range requests for video; arbitrary filesystem paths are not API inputs. Uploads validate type, decoded dimensions, size and path containment. API defaults to loopback with origin/host checks and CSRF protection for mutations. Browser access does not confer permission to execute arbitrary shell commands. LAN exposure is a separate explicit configuration with authentication, not a default bind to all interfaces.

## E. Shot direction, ratio composition and reference conditioning

The shot planner uses current panel crops, approved reading order, adjacent context and verified script facts. Broader chapter context may resolve identity or location; do not import future plot events into an earlier shot. Generated descriptions must separate source-supported facts from harmless visual completion. Unsupported new actions, participants, outcomes or dialogue are prohibited.

Each shot proposal includes:

- Source panel/crop IDs and ordered beat IDs; participants and exact approved asset revisions.
- Story purpose; starting state; action and ending state; explicit forbidden changes.
- Shot size (close/medium/wide), subject scale/placement, camera angle and camera action with rationale.
- A timed action outline, target editorial duration and measurable dialogue constraints.
- Per-ratio framing and what surroundings need invention, with background reference bindings.
- Route `direct_r2v` or `composition_assisted`, plus the reason for the latter.

Defaults: reactions favor close/medium shots; interactions show the relevant participants; physical action shows enough body/environment to understand the action. These are editable heuristics, not hard-coded shot-size rules. Reframe each ratio independently; do not crop the landscape video into portrait. Preserve story-relevant content, not every original source pixel. Generate unseen surroundings consistently with approved locations; important newly invented assets return to approval before production.

For direct R2V, supply the manga crop as story/composition evidence and approved colored references as appearance evidence. Build a deterministic labelled reference manifest mapping input slot → artifact hash → semantic role/entity. Prompt text must refer to the exact installed node's reference identifiers. Verify multiple-reference capacity and socket behavior against the pinned ComfyUI schema; the existing single-image probe is not proof of multi-reference behavior. Do not silently drop references exceeding capacity or build an unqualified contact-sheet workaround. Split the shot, choose supported conditioning, or hold for review.

For composition-assisted shots, generate one ratio-specific colored staging image with approved references: suitable camera distance, expanded environment, cleaned text/borders and intended start pose. Review it as a candidate before expensive video work during qualification. Once the route is qualified, allow a project option to automate composition selection, while preserving required asset approvals and clip review. A composition is a separate artifact role, not evidence that every panel has passed an old KEYFRAMED state.

Test two precise adapter roles separately: composition as an R2V reference versus composition as an I2V first frame. Never relabel one as the other; their framing constraints differ. Pick the supported route with observed quality gains. Reuse existing image tooling only if it passes colored identity and reframing tests; no automatic restoration of the old color-master/outpaint pipeline.

## F. Motion, timing and editorial rules

Natural speed is an output-quality requirement, not a synonym for higher FPS or more optical flow. Frame interpolation does not fix slow action. An arbitrary faster playback flag is not a generation solution.

Prompt compilation should describe concrete motion and timing, such as “turns promptly, stops facing the other speaker, then speaks,” rather than only “dynamic/cinematic.” Default to stable framing or a short purposeful camera move; avoid routine slow push-ins, orbiting and languid pans. Negative-prompt instructions are used only if the selected backend actually supports them. Do not claim exact temporal control from natural-language timestamps.

Plan short editorial shots around action beats; the backend may require a longer minimum generation. Record separate requested editorial duration, supported generated frame count, chosen trim interval and final timeline duration. Use the pinned profile's real frame grid/min/max; never hard-code another model's limits into common planning. Short fast action can occupy a trimmed portion of a longer valid generation.

For speech, measure TTS and allocate line/event intervals across shots. One line may continue over a cut without being synthesized twice. If the action clip cannot cover required dialogue naturally, propose a supporting shot, revise the shot allocation or regenerate. Never freeze frames, pad video or slow it down to conceal missing duration. Trim changes must revalidate all line/event intervals and subtitle times.

A reviewed creative speed adjustment may be stored in an edit decision, but is disabled by default. Do not apply it to dialogue/impact-synchronized shots without reworking and reviewing their timing. Preserve raw video and record the transformation; do not misreport retimed footage as a raw natural-speed success.

Clip review compares source, approved references and candidate at 1× playback. Record separate outcomes for color/style, identity, story correctness, framing, body-motion pace and camera pace. Optional motion statistics are diagnostic only: a fast camera can hide a static subject; high motion can mean flicker. Human acceptance remains authoritative. These are creative/technical checks, not the removed content/age guard.

## G. Model and hardware qualification strategy

Keep the working H3 workflow as the control. “It ran” and “best quality” are different claims; there is no measured comparative quality ranking for this manga workload yet. Do not switch the backend as a presumed cure for slow motion.

The primary comparison is within H3: the currently working turbo route, the non-turbo baseline where feasible, and approved colored references with optional composition assistance. Capture actual model/encoder/VAE/LoRA hashes, ComfyUI/node versions, quantization, sampler/scheduler, steps, seed, conditioning order and compile manifest. Stock, pruned and fused variants are distinct profiles; no filename substitution to satisfy a missing model.

GPU scheduling must account for both GPU memory and the owner's 32 GB host RAM. Sequentially load local planning/image/video/TTS services as needed. Record idle memory, peak GPU allocation, peak host memory, load time, generation time, download/decode time, swapping and failures. Do not leave a large planning LLM resident while video generation needs memory. Offload is allowed where measured behavior is acceptable. Do not infer speed from advertised low-VRAM support or assume a quantized format has native acceleration on this GPU.

An optional fallback experiment is Wan2.2 TI2V-5B with an approved composition input. Its official example documents a 24 GB GPU path using offload and CPU text encoding; this is evidence for a candidate, not proof it fits this machine's 32 GB host RAM or matches H3 quality/speed. See [Wan2.2 official inference instructions](https://github.com/Wan-Video/Wan2.2#run-text-image-to-video-generation). Keep this outside the critical implementation path. Recheck current official requirements before provisioning; do not add a model zoo, cloud fallback, or a second model by default. H3 integration details should be verified against [MiniMax's integration repository](https://github.com/MiniMax-AI/awesome-minimax-h3-integration) and the pinned installed ComfyUI source.

Bounded experiment, run programmatically:

1. Preserve one successful current probe and its exact settings as control evidence.
2. Select three source cases: close-up, interaction with a recurring object/location, physical action. Include the same character across cases; approve required assets first.
3. On one representative case, compare direct panel alone, direct panel plus colored references, and composition-assisted generation at one fixed seed and landscape ratio. Fixed seeds aid within-profile comparisons but do not equate different model architectures.
4. Extend the best plausible route and the direct-reference control to all three cases in both ratios. Reuse exact valid prior runs; record every failure and rejected candidate, not only winners.
5. Use a second seed for the provisional winner on all three cases/both ratios before selecting a default. Compare non-turbo only on a bounded case if quality defects plausibly relate to distillation; compare Wan only if H3 remains unsatisfactory or resource-infeasible.
6. Human reviews the criteria in §F, records accepted/rejected reasons and accepted-output rate alongside total GPU time per accepted clip. The optional image stage must justify its extra cost through observed improvement. No unbounded automatic seed search.

Design default retry budget: one initial candidate plus at most one explicitly enabled automatic quality retry; otherwise hold for review. Transport uncertainty never consumes this as permission to resubmit. A new quality attempt requires a terminal reconciled prior attempt and a recorded prompt/seed/route change. The owner has not set a time SLA; show measured times and configurable limits rather than inventing one.

## H. API, concurrency and agent contract

Expose versioned endpoints under `/api/v1`; generate an OpenAPI schema from the typed API. UI and agents use identical services. Proposed operation groups:

| Group | Minimum operations |
|---|---|
| Projects/chapters | List/create, import, inspect order and revisions, propose/review order. |
| Assets | Inventory, upload/propose revision, inspect candidates, approve/reject/revoke, bind occurrences, dependency impact preview. |
| Shots | Propose/list/read/edit plan revisions, regroup/split, inspect coverage, edit ratio specifications and reference manifests. |
| Runs | Plan/dry-run with blockers and reused work, start, status/events, request pause/release, resume, review ambiguous attempts. |
| Outputs | Stream artifact, list candidates, compare metadata, accept/reject, create edit decision, assemble/export status. |

All mutations include expected revision; stale edits return conflict with current revision and affected fields. Dispatch operations accept idempotency keys persisted with request hash/result; same key/different payload is a conflict. Network retries must not create duplicate work. Async requests return operation/run ID with durable state, not a blocking HTTP generation response.

Use persisted monotonic event IDs with polling or SSE reconnection; frontend subscriptions are presentation only and cannot own work. Refreshing/closing the browser must not interrupt a run. Report unavailable services, review blockers, held uncertainty, requested pause and verified release distinctly. No fabricated percent complete for an opaque backend.

Separate editing from GPU execution ownership. UI can edit new revisions during a run; the running plan remains frozen, and promotion checks whether it is still current. Starting another run either queues explicit work under the one owner or returns a clear conflict. Any takeover/kill confirmation names the exact owner process/run and is revalidated before action. Closing the browser never counts as approval. Multiple tabs are permitted; concurrent execution owners are not.

On backend/worker restart, reconcile database attempts with remote queue/history before dispatch. Reuse the existing durable intent and fencing rules. ComfyUI request acceptance uncertainty holds work for review; it is not an automatic quality retry. A UI “Retry” action must explain when an ambiguous attempt still blocks it. Pause drains by default and verifies unload/release; terminating a local HTTP waiter does not free the remote GPU.

## I. Ordered implementation milestones

Each milestone is a coherent production change with durable feature tests and maintained documentation. Update this checklist as work lands; do not mark hardware acceptance complete from mocked tests. Implementing agents must inspect newer main before each milestone.

### R0 — Baseline and reconciliation of existing contracts

- Inventory existing migrations, registry/reference features, run controls, API absence and tests. Verify reported P01–P12 implementation against actual callers, not historical test counts.
- Preserve a working probe invocation and owner-selected evidence locally; no user media or generated evidence committed to git.
- Identify exact panel-only SQL constraints and stage-enum gates that prevent shots/direct R2V. Plan additive migrations and rollback before editing those paths.
- Remove contradictory current-facing documentation as it is touched; historical contracts below remain subordinate.
- Exit: reproducible offline baseline and a migration/workflow compatibility map in maintained docs. No reimplementation of already-working storage/ownership.

### R1 — Versioned assets, shots and migrations

- Implement entities/invariants in §C, store APIs, typed payloads, dependency fingerprints and promotion predicates.
- Migrate existing character/variant approvals without inventing approval evidence; expose old references through the new asset abstraction. Preserve old run/output records and artifact paths.
- Add coverage validation for one-to-many and many-to-one source mapping, dialogue uniqueness and per-ratio completeness.
- Tests: migration on populated state, interrupted migration, stale revision conflicts, exact variant binding, revoked approval during execution, targeted invalidation and unchanged reimport.
- Exit: pure/store-level planning can represent a multi-panel shot and two shots from one panel without data loss; missing approvals block only production-consistent work.

### R2 — Local API and asset approval vertical slice

- Add shared application services, same-origin API/static hosting and project/asset screens. Prefer existing UI style and keep keyboard-accessible controls.
- Support official uploads and manual candidate registration first; connect local proposal generation in R3. Include approve/reject/revoke, variant binding and impact preview.
- Add upload/media containment, origin/CSRF protections, expected revisions, idempotent mutations and typed errors. Keep controls operational while execution lock is held.
- Tests: API permission/origin boundaries, invalid uploads, two-tab approval conflict, exact selected revision approval, preview separation, media range streaming. Browser test the complete upload→review→approve→bind path against the API.
- Exit: owner can approve all required asset kinds in a local browser without ComfyUI; agents can perform equivalent draft operations and read blockers. Human approval is never simulated by the agent.

### R3 — Local reference proposal and multi-reference qualification

- Add a bounded local image proposal profile and durable generation jobs for character/object/background candidates; inspect existing image adapters before selecting a new one.
- Extend H3 qualification to explicit multiple-reference manifests, schema/capacity checks and role-aware prompts. Preserve current single-panel probe behavior.
- Run §G steps 1–3 on hardware; record identity, colors, framing, memory and speed evidence outside git.
- Tests: reference slot order, rejected/unknown bindings, capacity overflow, candidate-generation approval-cycle avoidance, lost submission response, out-of-order stale completion.
- Exit: a colored approved character can condition two different source shots with recorded visual review. If not, keep the route unqualified and use the bounded composition experiment; do not claim prompt text alone anchors identity.

### R4 — Storyboard, shot planner and ratio specifications

- Implement §E as a typed local proposal followed by deterministic coverage validation. Retain required script translations and line IDs.
- UI edits shot grouping/order, intended actions, camera/framing, source links and per-ratio preview specs. Show inventions/uncertainties and missing references explicitly.
- Plan direct and optional composition routes without forcing image work on every panel. Add per-ratio composition preview/review and immutable edit history.
- Tests: new-event rejection/hold from unsupported proposals, omitted/duplicated beats, duplicated dialogue, future-context leakage fixtures, ratio-specific invalidation, mixed routes within one chapter.
- Exit: an approved shot plan covers a chapter's required beats and exposes every generation input before GPU dispatch. Semantic validation remains review-assisted, not a promised automatic proof.

### R5 — Production R2V execution and resumable web runs

- Implement shared orchestration over existing work/attempt storage; direct R2V does not require KEYFRAMED. Incorporate optional composition dependencies only for selected shots.
- Wire single supervised worker ownership, API/CLI control, queue/status events, resource lane handoff and immutable output promotion.
- Qualify actual supported H3 multi-reference/optional I2V roles and retain exact profiles; no silent backend fallback.
- Tests: browser refresh/disconnect, API restart, worker crash at intent/acceptance/download/promotion, idempotent double start, conflicting CLI owner, uncertain submission, approval change mid-run, drain/unload/resume and independent ratio retry.
- Exit: one shot produces two independently resumable candidate outputs through UI/API with no manual ComfyUI graph editing; failures remain actionable and honest.

### R6 — Motion review, candidate selection and bounded retries

- Compile action/camera timing prompts, implement candidate comparison at 1× speed with contact sheets and separate quality reason fields.
- Add deterministic trim/edit manifests and a disabled-by-default explicit creative retime control. Respect §F speech/event constraints.
- Implement configurable candidate budget, recorded changes between attempts, terminal-attempt checks and clear held outcomes.
- Complete §G steps 4–6; select route defaults from observed accepted-output rate and cost. Optional fallback benchmarking must not delay functional UI delivery.
- Tests: repeat retry clicks, budget exhaustion, ambiguous prior attempt, invalid trim windows, event lost by trim, reviewed retime provenance and unchanged raw artifact hashes.
- Exit: accepted samples show colored continuity, correct framing and non-sluggish body/camera motion across three cases/both ratios, with reviewer evidence. Failure means unqualified, not a hidden relaxed threshold.

### R7 — Shot-based audio, subtitles and final assembly

- Adapt measured dialogue timelines, local TTS, subtitles, SFX/BGM and final assembly to selected shot/edit revisions. Keep Japanese dialogue and English/Simplified Chinese subtitle requirements.
- Handle dialogue over cuts and several panels in one shot without repeated lines. Plan more footage when needed instead of padding/slowing video.
- Convert model-supported generation dimensions to exact delivery ratios through a documented reviewed framing transform: prefer sufficient generated coverage, no stretching or essential-content clipping. Nominal 864×480 is not exactly 16:9; validation must distinguish native canvas from exact delivery canvas. Qualify final upscale/encoder separately.
- Tests: real FFmpeg fixture with grouped/split shots, trim offsets, cross-cut dialogue, exact frame/sample coverage, both ratio exports, subtitle correctness and stale selection invalidation.
- Exit: a small chapter exports both exact-ratio videos with current accepted clips, required audio/subtitles, technical validation and a complete provenance manifest.

### R8 — Local web workflow completion and operational qualification

- Finish chapter dashboard, source/order review, approvals inbox, storyboard, queue controls, clip review and exports. Display specific blockers and next actions.
- Add hardware qualification profile, configurable time/memory budgets and verified pause/release/resume on the actual 3090 setup. Do not automatically kill unrelated GPU applications.
- Browser/API end-to-end test: import→review→approve assets→plan→render→pause/restart/resume→accept→export. Repeat with a changed asset revision and ensure only affected work changes.
- Document installation, model provisioning, one-command local launch, programmatic use, recovery and backup. All examples use local endpoints and preserve state.
- Exit: owner completes this path without using ComfyUI manually; agent automation exercises the same endpoints. No promise that pending hardware/visual gates have passed.

### R9 — Cleanup and release

- Remove obsolete mandatory stage gates, unused duplicated scheduling paths and stale instructions only after call-site/test audit. Keep useful qualification probes and long-term regression suites.
- Retire unused graph/config files only when no supported CLI/profile/run provenance depends on them; preserve necessary old manifests or explicit compatibility readers. Do not delete accepted media or user references.
- Run the full Python suite/lint, frontend type/build tests, API/browser integration tests and meaningful FFmpeg tests. Record hardware qualification separately from CI.
- Update PLAN/DECISIONS/README/test guide to describe shipped behavior. Commit production code, maintained docs/config and durable tests only.
- Exit: a reproducible release with no contradictory default paths and explicit known limitations.

## J. Release criteria, deferred work and next-agent checklist

The release is complete only when approved colored identities and important environments/objects are consistently represented, story beats are preserved, both ratios have independently accepted outputs, motion avoids unwanted sluggishness, and the entire workflow is manageable through the local web app and API. Technical checks do not substitute for visual acceptance. Persist required review outcomes per artifact revision.

Deferred: multi-user/cloud hosting, distributed workers, public remote access by default, arbitrary node editors, automatic model marketplaces, character LoRA training, mandatory per-panel colorization, automatic optical-flow quality rejection, pose/motion capture pipelines, and a rewrite of the Python backend. These may be considered later only for a measured gap. The allowed supporting movement does not authorize invented story events.

First actions for the implementing agent:

1. Read current main, DECISIONS and this active section; reconcile new changes before applying R0.
2. Verify the working single-panel probe remains intact. Preserve owner data and do not assume tests imply GPU access.
3. Deliver R1 then R2 as the first useful product slice; R3 can qualify media behavior before broad orchestration work. R4 depends on the R1 model; R5 depends on R3/R4; R6–R9 follow in order.
4. Do not re-ask settled creative questions. Missing runtime paths/model files/approval actions should surface as targeted setup or review tasks, not architectural blockers.
5. Keep H3 primary; do not claim a model ranking, a runtime SLA or 32 GB RAM fit without measured evidence. Report uncertain quality/resource findings and retain bounded alternatives.

---

## Retained earlier engineering contracts — subordinate to A–J above

The following 15–19 September handoff is retained for detailed recovery, storage, audio and testing contracts. Its numbered P tickets and baseline gap lists are historical, not an instruction to redo completed work. Where it conflicts, use the active R milestones above and current DECISIONS. In particular disregard its no-web-app default, mandatory keyframes, one-panel/one-clip coverage, and no-existing-output assumptions.

# Historical v1 implementation handoff (superseded where noted)

> **Direction update — 19 September 2026:** The owner now requires direct manga-panel R2V, without colorization or generated/outpainted keyframes. This supersedes all color-master/keyframe prerequisites and corresponding P09 work below. Preserve the other storage, approval, audio and recovery contracts. The owner reports actual video tests with poor quality; the earlier no-run baseline is historical, so do not discard current runtime work. Start with [the standalone R2V test guide](docs/r2v-workflow.md). The new graphs await hardware/output validation; the existing application render CLI still requires I2V keyframes. Integrate direct panel artifacts into a distinct R2V role after the output test, without falsely advancing panels to KEYFRAMED. The remaining detailed sections describe the earlier implementation plan where they conflict with this update.

**Prepared:** 15 September 2026
**Repository:** [lwk7454/manga-animator](https://github.com/lwk7454/manga-animator)
**Verified baseline:** [`01666663326be2b0bb2ac1d15fe27021e9e7b111`](https://github.com/lwk7454/manga-animator/tree/01666663326be2b0bb2ac1d15fe27021e9e7b111)
**Status:** Maintained repository implementation plan. Design contracts describe the target, not completed features. The documentation/test cleanup has landed; P01–P14 remain implementation/qualification work.

**Implementation progress (updated 16 September 2026, local in-session agent):**
P00–P08 and the offline halves of P09–P12 are IMPLEMENTED with durable
feature tests (275 offline tests + ruff green at `ef01272`). Specifically:
P01 transactional SQLite store, P02 command ownership, P03 revisioned order
review (D03 violation fixed), P04 artifact promotion + orphan recovery,
P05 resource lanes + honest pause/release, P06 typed script substeps,
P07 registry/sheet chains/exact-reference bindings, P08 pins + verification-
vs-qualification gates, P09 fit/pad/mask + exact frame plans, P10 TTS seam +
sample-exact timelines, P11 real ffmpeg mixer (BGM 0.25, asplit ducking fix),
P12 ffprobe validation + encoder policy + exact assembly manifests.
Remaining: P13 (unified `run` over the new machinery), P14 (release), and
ALL hardware qualification (§§24–25) — the 3090 is reserved by the owner;
no live probe has run. Records to land evidence now exist (P08 qualification
table, P04 artifact store, P05 release observations).

**Purpose:** Give another implementation agent a sufficiently explicit plan to finish the generator without silently changing the owner's decisions. This is a design handoff, not an implementation or hardware-validation result.

## 0. Instructions to the implementing agent

Read this document completely before changing architecture. Read the current repository's applicable instructions and `DECISIONS.md`, which records the current owner decisions. Confirm the current `main` HEAD. If it has advanced beyond the baseline, inspect the intervening changes and map them onto this plan; do not overwrite them or repeat already-completed work.

Authority order: the owner's subsequent instructions; the settled owner decisions in §1; the contracts and design defaults in this handoff; older repository plans/research. A historical claim of a successful live run is not evidence: the owner explicitly says this system has never run and there is no generated work to migrate.

Implement production behavior and durable feature tests. Maintain this plan in `PLAN.md`. Do not commit duplicate handoff copies, review reports, scratch probes, generated test results, screenshots, database files, caches, model weights, or sample owner media. Documentation may enter the repository when it is maintained operator/developer documentation. Keep useful existing tests in feature suites; do not replace real regression assertions with weaker happy-path assertions.

The owner previously authorized tested implementation commits directly to `main`. That does not authorize terminating a running command without the runtime approval described here, guessing credentials, deleting user inputs, or bypassing branch protections. Prefer small coherent commits. If a change cannot be delivered coherently in one commit, implement behind an explicit internal boundary and make the compatibility behavior safe before switching callers.

**Execution discipline:** For each milestone, implement its contracts, run its specified tests, inspect the resulting diff, update durable documentation, and record the acceptance result in the handoff/status response. Do not proceed through a failed dependency gate by silently weakening it. A hardware-blocked milestone may be implemented and offline-tested, but must remain visibly unqualified.

### Reading map

| Need | Sections |
|---|---|
| Settled requirements and scope | 1–3 |
| Architecture, data model and invalidation | 4–6 |
| Ownership, pause, recovery and CLI | 7–10 |
| Ingest, review, scripting and references | 11–14 |
| Model qualification, images and media | 15–19 |
| End-to-end run behavior | 20 |
| Ordered implementation tickets | 21 |
| Durable testing, hardware acceptance and release | 22–25 |
| Original finding coverage and drift checklist | 26–27 |

## 1. Settled owner decisions — do not reopen by default

| ID | Requirement | Consequence |
|---|---|---|
| D01 | No existing generated work; the system has never run. | Do not build a bespoke migration/repair tool for historical jobs or outputs. Still implement schema migrations for future versions and refuse destructive initialization over nonempty unrecognized data. |
| D02 | Imports append new pages; unchanged reimports do nothing. | Directory synchronization and deletion are not implicit. Replacement/removal must name an explicit operation and show affected work. |
| D03 | Order review changes reading order while preserving panel identity. | Never rename panel IDs or crop paths when sorting. Boundary editing is deferred, but revision/invalidation rules must support it. |
| D04 | Every configured ratio is required for aggregate completion. | Work and retries are independent per ratio. Adding a ratio does not redo valid shared work or completed other ratios. |
| D05 | Unapproved references are allowed only for an explicit preview. | Production claims of reference consistency require approved character/variant bindings. Preview and production outputs occupy different namespaces and cannot be substituted implicitly. |
| D06 | Reconcile ambiguous submission first; hold unresolved cases. | No blind automatic resubmission when the remote service may have accepted an attempt. |
| D07 | One active pipeline CLI command; commands originate from the same system. | Implement a local command owner, not a distributed multi-worker scheduler. Conflicting commands need user approval before stopping the owner. Controls remain available. |
| D08 | GPU is shared; work must pause, release resources and resume. | Drain/checkpoint by default. Report resource release truthfully. Immediate stop can require restarting an unfinished inference attempt. |
| D09 | Offloading is a performance concern, not an absolute prohibition. | Prefer GPU execution and avoid large CPU/RAM-resident LLM workloads. Qualify speed and memory on hardware rather than banning every offload operation. |
| D10 | All generation stays local, including text and translations. | Preserve endpoint restrictions, no cloud fallback, no inherited cloud credentials, no environment-proxy or redirect escape. Downloads of user-selected model assets are provisioning, not generation. |
| D11 | Remove automated age/content scoring and cloud Llama Guard. | Do not reintroduce them under a new name. Manual source/output review remains. Operational validation checks correctness, not content eligibility. |
| D12 | Required translations block script completion. | Persist and retry missing substeps; do not repeat successful caption/OCR/motion/other translations merely because one translation failed. |
| D13 | Production code and long-term tests belong in commits. | Keep regression coverage organized by behavior; the review-specific test file has been split into feature suites. Keep research/review-specific temporary artifacts outside the repository. |

### Preserved product requirements from the repository

These remain in scope unless the owner changes them: Python/Typer/Pydantic orchestration on the Mac; one Windows GPU box; local source/media files under the configured data directory; Japanese source/voice support first; English and Simplified Chinese subtitles; stable character appearance and voice references; independently generated landscape and portrait video; chapter-level deliverables; measured-audio-first sizing; no padding or speed-adjustment of rendered video to conceal duration errors; soft BGM with resting gain 0.25 and speech ducking; manual review checkpoints; centralized encoding; optional chapter merge.

Mandatory-scoring acceptance steps and cloud-text allowances from the old plan are superseded. Model names, installation claims, throughput figures and VRAM claims in historical research are candidates to verify, not proof.

## 2. What the baseline already contains

Do not rewrite these simply because their code predates this plan:

- Domain models, package-root-relative configuration, source layout, detector/order proposal, local review HTML and CLI commands.
- ComfyUI upload/submit/poll/download primitives; declarative workflow/profile loading; frame-grid ceiling helpers.
- Colorize/keyframe/render planners and a polling monitor, but with old queue dictionaries and enum-based completion.
- Corrected stock/fused render bindings and structural video-slot checks. This is not live qualification.
- Monitor final-running-job drain, known-offline hold, and pending-only handoff with failure hold. This is not complete ownership or recovery.
- Local-only LLM/ComfyUI endpoint checks and disabled proxy/redirect routing; local text defaults reuse the configured vision server.
- Removed automated scoring; `gate` is only a compatibility transition, and scripting accepts ORDERED panels directly.
- Script input fingerprints, partial-result retention, translation validation, checkpoint-before-panel-advance, and `script_incomplete` chapter state.
- Corrected chapter-number parsing, some chapter/ratio selection, canonical candidate/image pairing, component-verification return semantics, and converter cycle detection.

At the baseline, the previous implementation turn reported **144 offline tests passing**, Ruff passing and whitespace checks passing. Verify the suite in the new agent's environment; do not treat that count as a future quota or proof of live generation.

### Confirmed remaining boundaries

Whole-file JSON mutations and queue snapshots are still unsafe. IDs change during order review. Incremental import can remove records. Queue history blocks retries. One ratio can falsely complete a panel. Jobs do not consistently identify their series. Downloads can overwrite newer outputs before staleness is checked. Submission uncertainty is not durable. Profile pin sources conflict. Approved variant sheets are not bound. Outpaint preprocessing/graph paths are not trustworthy. Command ownership and resource-releasing pause/resume are missing. Audio/video/encoder modules are largely stubs. There is no complete chapter orchestrator.

## 3. Definition of finished v1 and deliberate scope limits

### 3.1 Required final behavior

A locally imported, manually reviewed small Japanese chapter must produce one final video for each configured ratio with:

1. Every required page/panel included exactly once in approved order.
2. Approved reference bindings for applicable characters and stable approved voice bindings for spoken dialogue.
3. Full-source-preserving ratio-specific keyframes and actual generated motion.
4. Measured, intelligible dialogue audio, configured local SFX/ambience/BGM, and no unintended model-generated soundtrack.
5. English primary subtitles and a selectable Simplified Chinese subtitle track, plus editable subtitle sidecars.
6. Correct video dimensions/fps/frame count, decodable output, and a provenance manifest identifying inputs, profiles, revisions and accepted artifacts.
7. Pause/drain/unload/resume, process restart and remote-service interruption without losing accepted work or duplicating ambiguous work silently.
8. Clear status and actionable recovery/review instructions.

Use a synthetic two-panel/two-ratio fixture first, then a small real chapter, then a full chapter chosen by the owner. Do not impose the old report's panel counts as a user requirement.

### 3.2 Explicit design defaults selected for this implementation

These are implementation defaults, not measured facts or owner-specified performance promises:

- SQLite is the sole metadata authority; ordinary files hold media and immutable JSON artifacts.
- macOS/Python 3.11 is the orchestration target. Linux is supported for development/offline and real-ffmpeg tests. Native Windows orchestration is not a v1 deliverable; Windows hosts GPU services.
- One foreground `koma run` process owns execution. No new daemon, Redis, message broker, distributed worker pool or web application framework.
- Existing human-readable panel IDs may be minted once at detection and retained forever. Reading order never changes them. New pages also have opaque stable page IDs.
- Default ratios are 16:9 and 9:16; nominal render fps is 24, represented as a rational. Required profile capabilities are checked before this value is accepted for a run.
- Final delivery is 1920×1080 or 1080×1920, with Real-CUGAN enabled by default for final delivery. Native-resolution preview is an explicit separate mode. The selected upscale implementation must be qualified before final delivery can claim completion.
- Default primary subtitle is English, burned in; Simplified Chinese is selectable in MP4 and supplied as an ASS/SRT sidecar. Do not burn both languages stacked by default.
- Use hard cuts between panels in v1. No crossfades that silently alter audio-first timing.
- No automatic recurring background run or boot scheduling. `run` and explicit `resume` drive the process.

### 3.3 Deferred, without hiding v1 gaps

Defer ref2va regeneration, previous-clip continuity, alternate painter adoption, alternate emotional TTS engines, generative ambience, arbitrary vendor-workflow compatibility, third-party package extraction, a general plugin platform, automatic user-idle detection, and an ever-growing single series video.

Implement GPT-SoVITS as the first TTS adapter because it is the named existing primary. Do not silently switch to another engine if qualification fails. Local curated asset libraries suffice for SFX/ambience/BGM; do not invent/download a library without provenance. Basic explicit model provisioning, profile pins and a local probe command are required; a generic model marketplace and rich A/B UI are not.

## 4. Target architecture and module ownership

The flow is: CLI → application services/planners → transactional store and provider adapters → validated artifacts → current-output pointers. Business logic never makes its correctness depend on a progress enum comparison.

| Location | Responsibility | Existing code to preserve or move |
|---|---|---|
| `cli.py` | Parse selections, display plans/status, collect explicit approvals, map domain outcomes to exit codes. | Existing commands remain recognizable; remove inline scheduling and persistence orchestration. |
| `application/context.py` | Construct resolved Settings, store, local transports, profile resolver, resource manager and cancellation/control interface once. | Preserve package-root resolution and explicit `--data-dir`; eliminate hidden data-root disagreements. |
| `application/run.py` | Build and execute a durable chapter/run plan, stop for reviews, resume by durable predicates. | Reuse stage functions behind planner interfaces. |
| `application/planning.py` | Pure selection, dependency requirements and work fingerprint construction. | Extract queue selection from `steps/*`. |
| `models/domain.py` | Typed series/page/panel/registry/script payloads. | Preserve useful validated structures; remove misleading authoritative job/progress duplication. |
| `models/work.py` | Typed work, attempt, error, artifact and run states. | Replace untyped queue dict conventions. |
| `storage/database.py` | Connection policy, schema versions and short transactions. | New SQLite implementation; no remote calls. |
| `storage/series_store.py` | Domain reads and transactional mutations, with expected revisions. | Preserve its public domain seam where useful; stop whole-index replacement. |
| `storage/artifacts.py` | Immutable artifact paths, validation records, dependency edges and promotion. | Replace writes to canonical output paths. |
| `scheduler/ownership.py` | Process-wide ownership, control requests, takeover validation. | Replaces implicit trust in one monitor. |
| `scheduler/queue.py` | Transactional work/attempt APIs, never load/modify/save snapshots. | Retain FIFO/deterministic priority as policy. |
| `scheduler/monitor.py` | Execute one scheduler tick and reconcile attempts through one implementation. | Remove completion logic duplicated in three stage modules. |
| `gpu/lanes.py` | Managed local-service handoff and verified resource release. | Preserve deployment-specific SSH knowledge behind configuration. |
| `gpu/worker.py` | ComfyUI transport only; typed remote outcomes and declared output selection. | Keep HTTP primitives; remove domain-specific colorize shortcuts. |
| `adapters/registry.py`, `adapters/compiler.py` | Typed profiles, compilation, qualification and component identity. | Preserve existing TOMLs/workflows and slot fixes. |
| `steps/ingest.py`, `review/sheets.py` | Input enumeration/detection and a small revisioned local review UI. | Preserve detector algorithms and dark-theme approach. |
| `steps/script.py`, `steps/cards.py` | Local proposals and typed substeps. | Preserve local-only behavior and partial retry tests; separate source extraction from identity binding. |
| `audio.py` | Measured event timelines, mixing and duration manifests. | Fill the existing seam. |
| `adapters/tts.py` | Local TTS transport/capability adapter. | New; no cloud fallback. |
| `video.py` | Per-panel delivery and chapter assembly/merge. | Fill the existing seam. |
| `encode.py` | ffmpeg/ffprobe invocation and one encoder/output policy. | Preserve planned VideoToolbox production choice. |
| `av.py` (removed) | The unused empty duplicate seam was removed during cleanup. | Use `audio.py`, `video.py` and `encode.py`; do not recreate a second composition system. |

Do not create a separate module for every trivial helper. The ownership boundaries above matter; exact splitting of small helpers within a listed module does not. Add dependencies only for a demonstrated need. SQLite, UUID/hash utilities and subprocess invocation are available in the standard library. A narrowly used process-inspection dependency is acceptable if required to verify process identity for takeover.

## 5. Storage and schema contract

### 5.1 Database and file policy

Database: `<data_dir>/state/koma.sqlite3`. Enable foreign keys on every connection, WAL journaling, a bounded busy timeout, and durable commits (`synchronous=FULL`). Use one connection per thread or an explicit serialized store worker. Never share a connection across threads accidentally. The database must be on the Mac's local filesystem, not a network share.

All read/check/write operations are one short transaction. Use `BEGIN IMMEDIATE` for claims and revisioned mutations. Never hold a transaction while calling a model, uploading, downloading, hashing a large file, decoding media, waiting for a process, or asking the user a question.

Use SQLite's schema version plus ordered application migrations, tested from the previous supported schema. Initial setup creates an empty database. There is no bespoke legacy import because the owner has no existing work. If a nonempty legacy store is discovered, stop with an explicit unsupported-store message and preserve it. Do not silently ignore or delete it. Generated test fixtures must initialize the new store directly.

Media and immutable payloads live under stable artifact directories. JSON exports are optional read-only snapshots, never alternate authorities. Existing `pages.json`, `script.json`, `queue.json` consumers must migrate to the store API. A temporary compatibility adapter may return old-shaped data, but writes must use transactions/CAS; it must not recreate lost-update semantics.

### 5.2 Identity conventions

- Internal IDs are UUID strings for series, pages, work, attempts, artifacts, reviews and runs.
- Series slug and chapter number are human selectors, unique in their owning scope. A slug change is not a v1 operation.
- `panel_id` is immutable and unique within its series. Existing `panel_key` formatting is acceptable for the initial ID, but its numeric suffix is a birth ordinal, not a mutable reading rank.
- A page has stable `page_id`, an immutable birth number, and an explicit sequence position. A panel stores `page_id`, bbox and explicit reading order.
- Store the owning series/chapter/page on jobs through validated foreign keys. Never recover ownership by parsing a display ID or by overriding it with `--slug`.
- Ratios are an enum. Store shared work with `ratio=''`, not SQL NULL, so uniqueness works predictably. Store preview/production mode explicitly.
- Revision tokens are integers for mutable domain aggregates; artifact/work input fingerprints are SHA-256 of canonical typed manifests. Never use Python's process-randomized `hash()`.

### 5.3 Normative logical schema

Implement the following logical tables. Names may be adjusted only together with every contract/test referencing them. The listed fields and uniqueness rules are required; add timestamps and foreign keys consistently. JSON payloads must validate against versioned Pydantic models rather than becoming arbitrary dictionaries.

| Table | Required fields and constraints |
|---|---|
| `series` | `id`, unique `slug`, `revision`, title, language, voice language, required ratios JSON, subtitle languages JSON, `video_profile_id`, production settings JSON. |
| `chapters` | `id`, `series_id`, number, revision, title; unique `(series_id, number)`. Store requested scope/settings; progress is derived. |
| `pages` | `id`, chapter/series IDs, birth number, sequence position, current source artifact ID, source revision, direction and confidence, order-review revision/status, deleted flag. Unique active sequence position per chapter. |
| `panels` | immutable ID plus owning page/series/chapter, birth ordinal, bbox, crop revision, reading order, deleted flag. Validate positive bbox and source bounds. Never reuse retired IDs. |
| `import_entries` | chapter, import-source identity, page ID, file/PDF-page occurrence key, source SHA-256, import operation ID. Unique `(chapter_id, import_source_id, occurrence_key)`. Enables idempotence without globally collapsing repeated identical pages. |
| `registry_revisions` | `entity_id`, `kind` (character/variant/voice), integer revision, typed payload, parent character ID when applicable, proposal/approved/rejected status, source evidence artifact IDs, approved-review ID. Unique `(entity_id, revision)`. Approved revisions are immutable. |
| `panel_bindings` | panel ID, binding revision, character ID/revision, variant ID/revision, relevant line IDs/voice bindings, review ID. Validate referenced revisions and approval status. |
| `reviews` | ID, series/scope, kind, expected aggregate revision, evidence manifest, proposed desired state, final decision/payload, actor, operation/idempotency key, timestamps. One decision per operation key; conflicts are explicit. |
| `runs` | ID, series/chapter selection, requested ratios/mode, immutable config snapshot/hash, status, requested control state, timestamps, last summary. Do not mutate an old run's configuration to mean a different request. |
| `run_work` | run ID, work ID, required output roles; unique `(run_id, work_id)`. Reused work satisfies multiple immutable run requests. |
| `work_items` | ID, series/chapter/panel scope, stage, ratio, mode, input fingerprint, desired spec JSON, state, priority, blocked reason. Unique `(scope_key, stage, ratio, mode, input_fingerprint)`. Shared work may satisfy multiple runs. |
| `attempts` | ID, work ID, attempt number, owner epoch, state, request identity, seed, compiled-workflow artifact/hash, upload plan, output prefix, remote prompt ID, server identity, error kind/detail, submitted/completed timestamps. Unique attempt number per work; at most one nonterminal attempt per work. |
| `artifacts` | ID, owning scope, kind, mode, producing attempt or deterministic operation ID, immutable relative path, SHA-256, size, input fingerprint, typed media/result metadata, validation status, creation time. Paths are unique. |
| `artifact_dependencies` | artifact ID → exact input artifact IDs and semantic roles. Unique edge; reject dependency cycles. Configuration/approval revision IDs are also in the manifest. |
| `current_outputs` | unique `(scope_key, stage, ratio, mode, output_role)` → accepted artifact ID and expected input fingerprint. This is the atomic current-output pointer; files never overwrite an older revision. |
| `profile_qualifications` | profile/workflow/component identity, server fingerprint, captured schema hash, probe artifacts and measured metadata, qualified/failed/unqualified status and reason. Unique identity tuple; changing any identity invalidates qualification. |
| `command_owner` | singleton per data root: owner UUID, PID, process creation identity, host ID, epoch, command/run ID, heartbeat, control endpoint; descriptive metadata only, paired with the real OS lock. |
| `control_requests` | request UUID, target owner UUID/epoch, action, supplied approval record when required, requested/acknowledged/completed state, response payload, timestamps. Idempotent and auditable. |

`scope_key` is a canonical application-generated key for a series, chapter, page, panel, line, segment or registry entity. Validate it against the explicit owning IDs. Line/segment scopes validate their parent panel and persist stable IDs in typed script/segment-plan artifacts. Cross-series dependency/binding relationships are invalid unless explicitly modeled as a shared immutable library asset.

Use SQL CHECK constraints for enums, positive revisions and valid numeric ranges; foreign keys for owned records; unique indexes for identity and current outputs. For the active-attempt rule, use a partial unique index over work ID for states considered nonterminal. Do not include human review holds in an automatic retry query.

### 5.4 Store API minimum

The following are behavior contracts, not a demand for these exact Python signatures:

```python
store.transaction()  # short, explicit transaction boundary
store.import_pages(operation, expected_chapter_revision)
store.set_page_order(page_id, expected_revision, desired_panel_ids, operation_id)
store.save_registry_proposal(proposal, evidence)
store.approve_registry_revision(entity_id, revision, expected_review_revision)
store.set_panel_bindings(panel_id, expected_revision, bindings)
store.plan_work(specs)  # insert-or-return exact logical work
store.claim_attempt(work_id, owner_epoch)  # atomic claim + immutable request intent
store.record_remote_acceptance(attempt_id, owner_epoch, prompt_id, server_id)
store.record_attempt_outcome(attempt_id, owner_epoch, outcome)
store.promote_artifacts(attempt_id, expected_fingerprint, validated_artifacts)
store.request_control(owner_id, epoch, action, operation_id)
store.derive_progress(selection)
```

All mutable methods must distinguish not-found, revision-conflict, invalid-transition and blocked-prerequisite outcomes. They must not silently retry a stale review request against newer data. Repeating the same successful operation ID returns the original outcome.

## 6. Artifact identity, dependency invalidation and promotion

### 6.1 Manifest and location

An artifact directory is immutable after validation, for example:

`series/<slug>/artifacts/<artifact_uuid>/payload.png`
`series/<slug>/artifacts/<artifact_uuid>/manifest.json`

Temporary downloads/encodes use a same-filesystem staging directory keyed by attempt and output role. Write manifest/media, flush and close, hash/validate, then rename into the immutable final directory. Only afterward commit the artifact rows and current-output pointers. A crash after rename but before DB commit leaves an orphan candidate that recovery can inspect; it does not destroy an accepted output.

The manifest records schema version, scope IDs, stage/role/mode/ratio, exact dependency IDs and hashes, configuration and approved-reference revisions, resolved profile/workflow/component identity, seed, request/attempt IDs, server fingerprint, measured output metadata and validation result. Never store credentials or signed URLs.

For final human-readable chapter filenames, copy/link from the accepted immutable artifact and atomically replace only the delivery alias after successful promotion. The DB artifact remains authoritative. An alias alone is never evidence of valid work.

### 6.2 Promotion preconditions

In one transaction, verify: attempt belongs to the current owner epoch or an explicitly adopted recovery attempt; work scope matches output ownership; required output roles all validated; mode matches; dependencies and approval revisions still match the desired fingerprint; no cancellation/invalidation superseded the request. Then record artifacts/dependencies and swap current-output pointers. A stale result is retained as historical/unselected, never promoted.

Multiple outputs from one attempt promote together. Do not declare a video attempt successful after downloading only a thumbnail or the wrong output node. Do not count a caught download failure as a successful download.

### 6.3 Invalidation matrix

| Change | Reuse | Invalidate/replan |
|---|---|---|
| Panel reading order only | Crop, caption/OCR, identity bindings, approved visual assets; context-free per-panel media. | Chapter ordering, subtitle offsets, assembly; any context-dependent script/SFX/BGM artifact whose manifest consumed order. |
| Page source or bbox revision | Unrelated pages/panels and registry revisions not derived from the changed image. | Crop and every actual descendant. Require new order/binding review when geometry or identity evidence changes. |
| Caption correction | OCR if independent and unchanged. | Caption-dependent discovery, bindings requiring reapproval, motion and context-dependent events. |
| OCR/line text correction | Visual crop/caption and independent color/keyframes. | Translation, speech normalization, TTS, audio timing, video lengths where audio duration changes, subtitles, assembly. |
| Translation-only correction | Original-language TTS and video when voice text/timing is unchanged. | Relevant subtitle payload/render and final assembly. If translated voice is selected, invalidate its TTS/timing too. |
| Voice/reference revision | Visual assets and other characters' utterances. | Bound utterance TTS, audio beds, frame plans when duration changes, affected clips and assembly. |
| Character variant/sheet revision | Unrelated characters/panels and source/OCR. | Only outputs consuming the old approved binding and their descendants. |
| Add a ratio | Shared artifacts and existing required-ratio outputs. | Missing ratio work plus aggregate completion. |
| Remove a ratio | Preserve its historical artifacts. | Requested completion set and final run manifest only. |
| Render profile/workflow/components change | Valid upstream masters/keyframes/audio unless their own profile changed. | Affected ratio renders, validation, upscale and assembly. |
| Subtitle style/font change | Source, TTS, video motion and clean upscaled frames. | Subtitle overlays/delivery encodes. |
| BGM gain change | Dialogue TTS and renders if the finite audio-bed duration is unchanged. | Mix and delivery mux/assembly. If duration changes, recompute dependent frame plans. |

Never invalidate by moving a panel backward in an enum. Mark desired fingerprints changed and let planners find missing current outputs. No automatic deletion of historical media is part of v1. Add an explicit dry-run garbage-collection operation later only if storage pressure justifies it.

## 7. One command owner and explicit takeover

### 7.1 Ownership scope

One mutating/executing Koma command may own the application on the orchestration host at a time, including across different `--data-dir` values. Use a host-user application-state directory for the global OS lock and rendezvous metadata; tests override that directory. Do not use a per-series or only-per-database lock that permits two data roots to use the same GPU concurrently.

Use an OS-held advisory lock (macOS/Linux file lock) for process exclusivity. Hold the open lock handle until the owner exits. PID text files and heartbeats are descriptive, not proof of exclusive ownership. Persist the owner UUID/epoch and run in its data-root DB. A tiny global rendezvous file may identify that DB/control endpoint; it is operational discovery metadata, not a second domain store. Write it atomically and validate it against the lock holder.

Owners: `run`, `monitor`, ingest/replacement/removal, review servers that can mutate data, profile/pin changes, retry/repair operations and manual stage execution. Short mutations acquire/release the same lock. Read-only status/plan/list commands may run concurrently. Pause/resume/stop/takeover are control operations addressed to the exact owner, not competing pipeline owners.

The global lock is local-host coordination, not a distributed lock. Running orchestration from two different Macs against the same GPU/data directory is unsupported and must be documented. Do not claim one process lock coordinates external applications using the GPU directly.

### 7.2 Conflict behavior

A conflicting invocation displays the current command, PID, start time, data root, run ID, run state and owned in-flight work. Without approval it exits with `OWNER_BUSY`. It must not kill the current owner, wait indefinitely, or quietly run concurrently.

Interactive takeover asks one concrete question identifying the owner and consequences: graceful drain/checkpoint/stop, or an explicitly selected immediate interrupt of unfinished owned work. Noninteractive callers must supply an approval token containing the expected owner UUID and epoch; a generic `--yes` is not enough to target a possibly changed owner.

After approval, send a control request to that owner. Graceful takeover is the default: stop new work, finish/checkpoint the current operation, release managed GPU resources and exit. The new command acquires the OS lock only after the old owner releases it. A timeout is a failed takeover, not permission to proceed.

If the owner is unresponsive, offer a separate force-stop approval tied to the same verified process identity. Validate PID, process creation identity, executable/command identity and owner UUID. If identity cannot be established, do not send an OS signal. Never kill by executable name or use `killall`. After force-stop, the new owner must reconcile remote attempts before any new submission. Killing the Mac process is not proof that ComfyUI stopped.

Approval prompts must live at the CLI boundary; domain code receives a validated approval record. Tests use explicit records without interacting with a human.

### 7.3 Review ownership

`run` does not hold the owner lock indefinitely while waiting for human review. It persists `waiting_review`, checkpoints, unloads managed GPU models and exits with a review-required outcome. `koma review` then acquires ownership and serves the local review UI. On completion/exit it releases ownership; `run` resumes from the saved predicates.

If the review UI offers a preview generation action, execute it through the review owner's application service and normal attempt machinery. Do not spawn a competing CLI command. Production generation never bypasses unresolved review by taking over its process.

## 8. Pause, resume, resource release and shutdown

### 8.1 Separate requested state from observed state

Persist a user's intent independently from the actual outcome. A control request is acknowledged immediately but not marked completed until the action happened. A run can be `pausing` while an inference attempt is still active. Do not print “GPU released” merely because a pause flag was written or a health request failed.

Run states: `created`, `running`, `pausing`, `paused`, `waiting_review`, `waiting_service`, `held`, `interrupted`, `failed`, `complete`, `cancelled`. Only `complete` means all requested accepted deliverables exist. Store `resources_state` and its observation timestamp/evidence alongside run status; resource status is not merely a transient display string. Control/status may additionally report resource state: `active`, `draining`, `releasing`, `released`, `release_failed`, `unknown`.

### 8.2 Default pause

1. Record `pause_requested` for the exact run/owner; scheduler stops claiming new inference/encode tasks.
2. Allow the current owned GPU request to finish. Continue polling/downloading/validation/checkpointing it. No second inference starts.
3. For local scripting, finish and commit the current bounded substep; do not launch every panel in advance. Stop admitting futures beyond the bounded active set. Default local-model concurrency is one.
4. For CPU encode/mix work, either finish the current atomic artifact or terminate the owned subprocess and remove its unaccepted staging output; choose the former for default drain. Never promote a partial file.
5. After all owned work is checkpointed, unload the managed active model service and verify release as described below.
6. Mark `paused` and acknowledge completion. The owner may stay alive waiting for resume and continue holding the lock; status/control commands still work.

`koma pause` waits for this result by default, while printing the in-flight stage and “draining.” A `--no-wait` option may return after acknowledgement, clearly saying “pause requested.” A bounded CLI wait timeout returns the real pending state; the owner continues handling the request.

### 8.3 Immediate pause/stop

`pause --now` means stop the owned unfinished operation promptly, preserve accepted work, and make the interrupted work retryable only after remote termination/reconciliation is established. It does not promise checkpointing inside a diffusion sampler or TTS inference. Explicitly display that the unfinished attempt may restart.

ComfyUI's interrupt/free behavior may affect its whole server. Before using a server-wide operation, verify the current remote job is the known owned attempt and there is no unrelated active workload that would be affected. Remove only owned pending queue entries when the provider exposes a scoped operation. If scoped interruption cannot be established, hold and explain the conflict; do not kill another application's job.

After interruption, an already completed and valid output may still be recovered and accepted if current. Otherwise the attempt becomes `interrupted` only after enough evidence exists that it is no longer executing. Lost contact leaves uncertainty, not a clean retry ticket.

`stop` is graceful pause plus owner exit; it preserves the run for later resume. `stop --now` uses the explicit immediate semantics. A cancelled run retains artifacts/history and is distinct from a stopped resumable run.

### 8.4 Resume and absent owners

With a live paused owner, `resume` sends a control request, rechecks prerequisites/server identity and resumes the saved plan. Without an owner, `resume <run-id>` acquires ownership and executes that run in the foreground from its stored configuration; it does not reconstruct settings from whatever defaults happen to exist now.

If pause/stop is requested after the owner died, persist paused intent and acquire a short recovery ownership session to reconcile/drain known owned remote work, without submitting anything new. If the box is unreachable, report `unknown` release state and keep paused intent. Do not report a freed GPU that cannot be checked.

A new run with changed configuration is a new request; it may reuse exact valid artifacts but does not mutate an old run's definition.

### 8.5 Managed service handoff

Resource modes: `none`, `llm`, `tts`, `comfy`, `upscale`. Only one GPU-consuming mode is admitted at once by the command owner. Local CPU-only OCR/metadata operations may run without GPU ownership if they do not contend with managed model processes.

For a mode change: drain owned work; verify remote queue ownership; request unload/stop of only Koma-managed services; require acknowledgement and observed termination/unload; start the next configured service; require readiness; then submit. Persist the observed service/server fingerprint and failure reason. No fail-open submission after a handoff error.

Replace hardcoded taskkill-by-name with configured managed service identities: scheduled-task identity plus a verified service PID/instance or a provider unload API. Never stop an unrelated `llama-server.exe` by its name alone. Probe process/model residency where available; a listening ComfyUI endpoint can be idle without loaded weights, and an unreachable health endpoint is not proof of release. Final pause qualification requires the owner's hardware: managed weights unload and memory becomes available for another application. Do not require total GPU usage to be zero.

## 9. Work/attempt state machine and crash recovery

### 9.1 Separate work from attempts

Work states: `blocked`, `ready`, `active`, `succeeded`, `failed`, `cancelled`, `superseded`. A work fingerprint identifies a desired result. An attempt is one execution of that result. A failed attempt never permanently deduplicates away the logical work.

Attempt states: `prepared`, `submitting`, `submission_uncertain`, `running`, `downloading`, `validating`, `succeeded`, `failed`, `interrupted`, `stale`. The first six are nonterminal for the unique-active-attempt constraint. A held uncertain attempt stays nonterminal; work is `blocked` with its reason until reconciliation resolves it. `submission_uncertain` holds the work; it is not eligible for normal retries.

The planning function is pure: select exact scopes, resolve approved dependencies/configuration, and return existing valid results, work specifications or typed blocked reasons. No uploads, random seeds, model calls or persistence inside pure planning. Persist work in a separate transaction.

### 9.1a Legal transition and fencing rules

Implement one transition validator used by all providers. The normal attempt path is `prepared → submitting → running → downloading → validating → succeeded`. A possibly dispatched `submitting` attempt becomes `submission_uncertain`; reconciliation may resolve it to running, downloading (already finished), or a proven terminal outcome. Remote validation rejection becomes failed with a nonretryable configuration reason. Known running failure becomes failed; verified cancellation becomes interrupted. A validated obsolete result becomes stale. Terminal attempts never reset to prepared: a permitted retry allocates a new attempt number. Deterministic CPU tasks use the same intent/outcome records but skip remote-only transitions through an explicitly declared local path.

Work moves blocked → ready only when prerequisites hold, ready → active on atomic claim, active → succeeded only on promotion, and active → failed/blocked according to the terminal/reconciliation outcome. Failed work becomes ready only through its bounded retry policy or an explicit retry decision. A new fingerprint is new logical work; obsolete work becomes superseded while any old live attempt remains subject to reconciliation. Do not erase its active-attempt record to enable a competing submission.

After an owner dies, the new OS-lock owner may adopt recovery responsibility for existing nonterminal attempts in a short transaction. Record previous/new owner epochs and recovery action; do not allocate a new seed/request or alter immutable request intent. Every later write checks the newly assigned recovery epoch. The old owner's callbacks can no longer promote. This is fencing of local state, not cancellation of remote execution.

### 9.2 Submission protocol

1. Claim work in a transaction, checking owner epoch and exact fingerprint. Allocate attempt ID and seed once.
2. Persist a stable request identity and deterministic upload/output names containing the attempt ID. Compile and persist the exact workflow JSON before submission. Upload names must be known/persisted before the upload side effect; update returned remote references before proceeding.
3. Mark `submitting` and commit before invoking `/prompt`.
4. On accepted response, immediately persist the prompt ID/server identity and `running`, independently of any later polling/submission.
5. A definite local validation error before request dispatch is safely failed. A connection failure proven before request bytes were sent can be retryable. A timeout/reset after possible dispatch, an invalid accepted response, or a process death in `submitting` becomes `submission_uncertain`.
6. Poll only when the server is reachable and its identity/capabilities are understood. Observe queue/history and output declarations, then download to attempt-specific staging. Validate and promote transactionally.

Do not use a random client ID regenerated at every retry as the only reconciliation identity. Include a stable attempt/request marker in the request fields the qualified ComfyUI version preserves, and in output/upload names. Verify preservation against captured queue/history examples and one live probe. If the server cannot preserve enough identity, uncertain submissions must remain manual holds.

### 9.3 Reconciliation evidence

On startup or after owner takeover, reconcile every nonterminal attempt before admitting new GPU work. Match by persisted prompt ID first. Otherwise match exact persisted request identity/workflow digest/output prefix in remote queue/history, rejecting multiple ambiguous matches. Output-name coincidence alone is not adequate evidence of ownership or completeness.

A known accepted prompt absent from both healthy queue and history may have been lost on server restart. Check the known output location/prefix and server epoch before marking lost. A server restart or history retention gap does not prove that an uncertain request was never executed. Validate recoverable outputs; otherwise hold uncertain cases. Definite lost accepted attempts can be retried once their old execution/output cannot later race promotion.

Manual resolution supports: adopt a verified remote prompt; attach/validate a known output; mark definitively not accepted with evidence; cancel/abandon the work while retaining history. Do not offer a generic “retry everything uncertain” command. If the owner explicitly authorizes duplicate-risk resubmission, record that exact decision and keep fencing so an old late output cannot overwrite the selected result.

### 9.4 Error taxonomy

| Kind | Default behavior |
|---|---|
| `configuration_invalid`, `profile_unqualified`, `input_missing`, `approval_required` | Block before submission; no automatic retry loop. |
| `service_unavailable` | Hold with bounded backoff; respect paused intent; retain attempt identity. |
| `submission_uncertain` | Reconcile; hold if still ambiguous. |
| `remote_rejected` | Record remote validation diagnostics; fix inputs/config before retry. |
| `remote_failed`, `remote_lost` | Preserve logs; retry only under stage policy after ownership is settled. |
| `download_failed` | Retry download/reconciliation of the same accepted attempt; do not regenerate automatically. |
| `artifact_invalid` | Retain rejection metadata; explicit bounded regeneration policy, not unlimited retry. |
| `stale_result` | Retain historical result; never promote; plan current work if needed. |
| `review_conflict`, `owner_busy`, `handoff_failed` | Report the exact blocker; do not reinterpret as a model failure. |
| `interrupted` | Resume from accepted checkpoints; unfinished inference may need a new attempt. |

Default retry policy: transient transport retry within a bounded request window; two automatic new attempts only for explicitly classified definite retryable execution failures; no automatic new attempt for uncertainty or configuration/approval failure. Make counts configurable and record attempt history. Never describe this as exactly-once remote execution.

### 9.5 Crash-window acceptance matrix

| Crash point | Required restart result |
|---|---|
| Before work transaction commits | No half-created work. Planning may recreate it. |
| After work commit, before claim | Ready work remains schedulable. |
| After prepared intent, before dispatch | Recover intent; verify no ambiguous dispatch before proceeding. |
| During upload | Reuse stable names and inspect/upload safely; never change the intended source revision. |
| After dispatch, before prompt-ID persistence | Submission uncertainty; reconcile instead of blind retry. |
| While running | Reattach to the same accepted prompt. |
| During download | Accepted remote work retained; incomplete staging is retried/discarded safely. |
| After immutable file rename, before DB promotion | Recover orphan candidate from its manifest or leave it unselected; old current output remains valid. |
| After DB promotion, before run progress update | Derive progress from current artifacts; no repeated inference. |
| After pause request, before release acknowledgement | Resume recovery in paused/draining intent; never start a new job. |
| After new input revision, before old attempt finishes | Old attempt may finish historically but cannot promote. |

## 10. CLI contract and machine-readable outcomes

Retain existing recognizable commands, but make every stage command use the same planners/store/scheduler. No alternative reconciliation path is allowed.

| Command family | Contract |
|---|---|
| `new`, `ingest`, explicit `replace-page` / `remove-page` | Validated scope, owner lock, transactional metadata. Destructive/replacement operations show dependent work before confirmation. |
| `plan <slug> --chapter N [--ratio …] [--preview]` | Read-only plan, dependencies, reusable artifacts, review blockers, unqualified providers and proposed work counts. No GPU/network generation calls. |
| `run <slug> --chapter N [--ratio …] [--preview]` | Snapshot request; reconcile old owned work; execute until complete, paused, blocked or review-required. Production is default. |
| `status [slug] [--json]` | Required/completed outputs per stage and ratio, active attempt, owner, control intent, release state, blockers; never just one misleading panel enum. |
| `review <slug> --kind order|production` | Revisioned local UI with desired state and evidence. Production review includes cast/variant/voice/line binding, not automated scoring. |
| `pause [--now] [--no-wait]`, `resume [run-id]`, `stop [--now]` | Owner-targeted semantics from §8. |
| `jobs list`, `jobs retry <work-id>`, `attempts inspect/resolve` | Exact work/attempt IDs and typed outcomes. No blanket deletion of history. |
| `models list`, `models verify`, `models probe`, `models pin` | Active-profile inventory, local qualification and explicit pins. Probe is explicit GPU work under ownership. |
| `assemble`, `merge` | Use validated current artifacts and frozen manifests, not directory glob order. |
| Existing `gate` | Optional compatibility no-op/transition; absent from the recommended normal flow. Never restores scoring flags. |

Support `--json` with a versioned envelope containing `schema_version`, `outcome`, `run_id`, `owner_id`, `summary`, `blockers`, `next_actions` and `artifact_ids/paths`. Logs go to stderr; machine JSON goes to stdout. Large traces remain in local runtime logs.

Exit codes: 0 = requested operation completed (a pause control can complete without the run being complete); 1 = execution failure; 2 = invalid invocation/configuration; 3 = review/approval required; 4 = service unavailable or unresolved held work; 5 = owner busy/revision conflict; 130 = user interruption. Commands must not print a success-looking “queue drained” and return 0 for a failed requested chapter. A monitor-only drain can report terminal failed work distinctly.

## 11. Ingest, repeated imports and source changes

### 11.1 Enumeration and import identity

Enumerate image directories deterministically with natural numeric order; accept supported image files only. Preserve caller-visible source identity separately from content hash. For a directory import, identify an item by a registered import-source ID plus normalized relative path. Re-importing the same source/path with the same SHA is a no-op. A changed hash at the same source/path is a replacement conflict, not an append or silent skip.

A distinct path may intentionally contain identical pixels. Preserve that distinct occurrence rather than globally deduplicating by SHA. If a user imports a moved/copy directory, show it as a new import source; optionally allow an explicit source-alias mapping. Do not guess that two directories mean the same chapter source solely because some hashes match.

For PDFs, register the PDF source identity and page ordinal. Use a fresh staging raster directory per import revision; enumerate raster pages numerically. An unchanged PDF hash is a no-op. A changed PDF requires explicit replacement/import-source handling because page ordinals may have shifted; do not silently replace the chapter.

Each appended page receives the next birth number/sequence position, stable page ID and immutable source artifact. Copy and hash the source into staging, decode/check dimensions, then commit metadata referring to the finalized source. A partial batch failure must preserve already committed pages and identify which input failed. Re-running the same operation must not duplicate committed items.

### 11.2 Detection and order

Keep the existing detector as the initial algorithm. Validate all bboxes against image dimensions and reject zero/negative sizes. Persist detector identity, confidence, fallback/splash status and direction evidence instead of dropping low-confidence information. Explicit series direction wins; an AUTO heuristic may propose direction, but uncertainty must be visible in review.

Mint panel IDs once. Store their birth ordinal and proposed order separately. Generate crops from the source/bbox revision and validate that each crop's manifest references the correct source/page/panel. The proposed order is not certified until reviewed.

Do not implement a new detector or long-webtoon segmentation engine merely to satisfy the architecture milestone. If the acceptance chapter reveals systematic detection failure, qualify a replacement through an isolated adapter and fixtures rather than altering identities silently.

### 11.3 Explicit replacement and removal

`replace-page` targets stable page ID, requires expected revision, shows affected artifacts, and stores a new source revision. If existing bboxes remain meaningful, preserve IDs and require renewed review; if redetection changes panel topology, retire old IDs and create new ones with a reviewed mapping. Do not pretend a visually different panel is the old one just because its index matches.

`remove-page` is a tombstone operation with explicit approval and dependency invalidation. Do not delete source/media files automatically. Boundary editing is not exposed in v1; store methods must already be able to increment crop revision and invalidate descendants when that feature arrives.

## 12. Revisioned order review

The review payload is a complete desired permutation of the page's active panel IDs, plus `page_id`, `expected_revision` and `operation_id`. Never send only the last selected pair of indices. Client state and rendered labels must derive from that same permutation.

Server validation before mutation: correct owning series/page; all active panel IDs exactly once; no foreign/missing/duplicate IDs; expected revision matches; review eligibility still valid. In one transaction, store order, increment review revision, record the decision and mark the source/order review satisfied. Do not rename IDs, move crop directories or touch bbox values.

Repeated operation ID with the identical desired order returns the original successful result. A stale expected revision with a different request returns a conflict and current state; never silently replay the edit. Refresh reloads the saved permutation. Two browser tabs editing the same page cannot overwrite each other silently.

Use stable page IDs in DOM IDs, selectors, payloads and routes. Display chapter/page labels to humans. Both page 1 of chapter 1 and page 1 of chapter 2 must work independently. Handle malformed JSON and domain errors with structured responses. Bind review server to loopback; validate same-origin requests or a local session token so unrelated browser pages cannot issue state-changing requests accidentally. This is operational integrity, not content filtering.

The UI remains small and dark themed. It shows original source, crop outlines, proposed order, uncertainty indicators and saved/unsaved status. Production code should not expose database or migration details in review flows.

## 13. Script substeps, dialogue structure and timed events

### 13.1 Retain the recent improvements

Do not regress local transport, no-score behavior, missing-translation blocking, input fingerprints, partial reuse or checkpoint-before-advancement. Port them to per-substep artifacts/transactions. The current single `script.json` fingerprint is a useful interim behavior, not the final dependency model: translation/style changes should not unnecessarily invalidate visual inference.

Each successful substep becomes an immutable typed JSON artifact with exact input dependencies. Checkpoint immediately after each completed substep; do not wait for a whole chapter or unbounded batch. On pause, finish/checkpoint the current substep. On process loss, only uncheckpointed substeps may repeat.

### 13.2 Required structured payload

For every panel, store:

- Stable panel/source/crop references and revision.
- Caption text with model/prompt identity.
- OCR lines with immutable line ID, source text, source-language tag, bbox/evidence when available, source ordering, confidence and optional manually corrected text.
- Speech normalization separate from verbatim source text. Never overwrite OCR with an invented dialogue paraphrase.
- Translations keyed by line ID and target language, each with its own valid/retry-needed status and dependency hash. Empty source dialogue requires no translation. A missing/blank nonempty-source translation is incomplete.
- Provisional speaker/entity mention IDs and evidence/confidence; a distinct unresolved state, not a guessed approved character ID.
- Motion description per requested ratio where different framing requires it. Keep appearance in the accepted keyframe; do not introduce contradictory appearance instructions.
- Event proposals for dialogue, SFX, ambience and BGM mood, with stable event IDs and symbolic timing anchors.
- Completion predicates for source extraction, required translations, identity/voice bindings and audio readiness as separate concepts.

A single aggregate SCRIPTED value cannot mean all of these are satisfied. Display their individual readiness and derive any compatibility enum.

### 13.3 Avoid a discovery dependency cycle

Initial extraction produces caption/OCR/translation and provisional speaker/character evidence without requiring approved cards. Discovery consolidates those proposals. The production review approves cards/variants/voice references and resolves panel/line bindings. Final speech/audio planning consumes those approved bindings. Do not require approved cards before generating the proposals needed to approve them.

Ambiguous speaker assignment blocks the relevant production dialogue task and appears in the production review. A user may explicitly mark narration, no speech, or an approved generic narrator voice. Never silently assign all lines to the first character or silently omit unresolved lines.

### 13.4 Timing anchors and text scope

Use integer audio sample positions for final timing, not floating-point cumulative seconds. Before TTS is measured, events refer to stable anchors such as `panel_start`, `utterance:<id>:start/end`, or `panel_end`, with explicit offsets. Resolve anchors after synthesis; reject missing or cyclic anchor relationships.

The v1 OCR/voice acceptance path is Japanese. Preserve language fields and configured subtitle lists. Do not claim Korean OCR/voice support until an appropriate local adapter is qualified. Unsupported language must yield an actionable capability blocker rather than silently treating it as Japanese.

Translation validation must check every required line ID exactly once and nonempty result for nonempty source, not only the count of returned rows. Keep a narrowly tested compatibility reader for the current aggregate response during transition, but the final stored schema is line keyed. Model output is data: validate schemas, references and bounds before persistence. No prompt content may select arbitrary filesystem paths or commands.

## 14. Character, variant, voice and production review

### 14.1 Registry lifecycle

Registry revisions move `proposed → approved` or `proposed → rejected`. Approved revisions are immutable. An edit creates a new proposed revision; it does not mutate a revision already consumed by outputs. The approval record includes the exact evidence, canonical source artifact, palette/reference sheet, voice reference and selected bindings that were reviewed.

Persist canonical candidates as paired IDs/artifact paths/scores, and persist the selected proposal. Reject a model's returned ID if it is not in the submitted candidate set. Candidate ranking must use deterministic narrative ordering for ties and must not depend on thread-completion insertion order.

A variant belongs to a character and has its own revision, descriptive payload and reference-sheet artifact. Panel bindings explicitly identify `(character_id, character_revision, variant_id, variant_revision)`. Line bindings additionally identify a voice revision. Background-only panels may explicitly bind no character; that is different from missing analysis.

### 14.2 Reference-sheet generation without circular approval

An approved source selection/palette can authorize a draft reference-sheet generation task. The resulting sheet remains a proposal until the user approves that exact artifact. Only then may production colorization consume it. User-supplied already prepared sheets can follow the same import/inspect/approve route. Do not silently pick the first available variant or a random pending palette.

For native-color source panels, the source-derived master may be a validated copy/alias. This shortcut does not require recolorization, but its provenance and any later identity bindings remain explicit. A character-reference consistency claim still requires the relevant reviewed binding; the copy shortcut is not an approval bypass.

### 14.3 Preview contract

`--preview` is explicit, appears in status, and writes preview work/artifacts/delivery files. Preview may use unapproved references and unresolved experimental profiles only when the specific preview plan states that limitation. It must still satisfy local routing, scope ownership, input validity and basic media validation.

Production never consumes a preview pointer as a current production output. Reusing preview bytes requires an explicit review/adoption operation that verifies exact source/profile/reference fingerprints and records required approvals; otherwise regenerate. No preview result marks the production run complete.

### 14.4 Production review content

One coherent review session covers cast/variant references, voice samples, unresolved speaker-to-line assignments, script corrections and local media asset selections. It is the second normal human checkpoint after order review. Per-output model qualification/error review may occur when needed, but do not require a redundant click on every successful technical validation.

Voice references need a playable preview, source/provenance information and the engine-specific transcript/language fields required by the qualified TTS service. Do not bundle fabricated voice-reference assets or infer speaker identity from a filename. With no approved voice/reference assets, the system must produce a clear setup/review blocker; it cannot claim a complete voiced chapter.

## 15. Model profiles, component identity and qualification

### 15.1 One profile authority

The series database record is the authoritative profile pin. Global configuration supplies the initial default only when creating an unpinned series. Remove competing reads/writes of a separate `pins.json`; do not maintain two writable sources. Each run freezes the resolved settings. Each work fingerprint includes the relevant profile revision, workflow hash, component identities and generation parameters, rather than the entire unrelated global configuration.

Provide explicit `models pin` behavior that reports affected stages before changing the series revision. Already accepted artifacts remain historical; only dependent desired outputs become missing/stale. Changing an upscale profile must not regenerate dialogue. Changing a video profile must not invalidate approved character sheets unless they actually used it.

### 15.2 Profile contract

Extend the existing registry/compiler instead of replacing declarative profiles with branches scattered across steps. A versioned profile must declare:

- Role: text/vision, character sheet, colorize, outpaint, video, TTS or upscale; provider and supported execution lane.
- Workflow/schema identity, required components and precise loader/input bindings.
- Supported dimensions/ratios, fps, duration/frame-grid constraints, precision and exposed generation parameters.
- Required injections, including source image, mask where applicable, approved references, prompt, seed, steps, dimensions and frame count. Every required injection must resolve to an actual executable graph input.
- Explicit output node IDs/slots, artifact roles and media types. Distinguish a loader filename from an IMAGE socket, and distinguish video files from latent tensors or preview images.
- Provider capabilities: request identity, history lookup, cancellation granularity, model unload and observable release. Unsupported capabilities stay unsupported; do not infer them from endpoint names.

Validate every node reachable from declared outputs against the captured installed schema, checking required inputs, links, enum values and output slots. Detect cycles. Reject unresolved placeholders. Prune unused nodes only when they are outside every required output dependency and have no required side effect. Compiler tests must inspect the effective sampler/latent path, not merely the presence of a replacement string elsewhere in the JSON.

### 15.3 Verification is not qualification

`models verify` checks component files, hashes, loader compatibility, workflow structure and configured service identity. `models probe` executes a bounded real example and records qualification. A profile cannot become qualified merely because files exist or a mocked response succeeds.

Record exact weight hashes, custom-node/provider versions or source commits, relevant runtime versions, graph/schema hashes, server identity, parameters, observed output metadata, wall time and available memory observations. Hash large assets outside database transactions. Persist runtime evidence under the data directory; commit only small sanitized fixtures that support enduring compiler tests.

Changing model bytes, effective workflow, custom-node implementation or hardware/runtime identity invalidates the relevant qualification. Service restart with the same installation requires healthy identity verification and remote-history reconciliation; it need not rerun all expensive probes if the qualified identity is unchanged. A lost remote-history epoch still affects attempts independently.

Do not invent node schemas, TTS endpoints or parameter semantics. Obtain them from the installed local service and its authoritative implementation/documentation. Any implementation done without that access must expose an explicit unqualified capability and a runnable probe procedure. Model provisioning is separate from generation: use owner-selected assets with recorded provenance, and never execute downloaded installation code implicitly.

## 16. Color masters, full-source keyframes and motion

### 16.1 Image stages

Keep a shared color master at the source crop's aspect ratio. Its inputs include crop revision, source interpretation and exact approved applicable reference revisions. An already colored source can follow an explicit identity/copy path with equivalent provenance; it still needs correct production bindings. Do not recolor separately for each ratio when a shared valid master exists.

For each requested ratio, create a canvas that contains the entire source image after uniform scaling. Place it deterministically, normally centered, and identify the newly exposed region with a mask. Preserve the source rectangle and transform in metadata. The actual outpaint graph must consume that image/mask and generate at the requested canvas dimensions. A center crop is prohibited. A Pillow resize/pad is valid preprocessing but must not be presented as completed generative outpainting.

Use a synthetic source with distinct edge/corner markers to test fit/padding math. Inspect compiled graphs for both orientations. Live probes must demonstrate that the intended source region reaches the model and that new area is generated. Strong composition changes are a visual-quality issue requiring calibration/review; structural marker tests alone cannot prove aesthetic preservation.

An unqualified or incompatible outpaint workflow blocks production for the affected ratio with an actionable reason. Do not silently substitute an unrelated model or letterboxed still and label it a generated keyframe.

### 16.2 Audio-first frame planning

Rendering depends on the measured content timeline in §17. Resolve profile fps and frame constraints before computing duration. Let `F` be a rational fps and `S` the required audio duration in integer samples at sample rate `R`. Compute the minimum frame count with exact integer/rational ceiling of `S * F / R`, then round upward to the profile's valid frame grid. Never round down. Include minimum/maximum supported frames and the selected grid in the plan artifact.

After generation, validate the actual decoded frame count/fps against this plan. Do not slow video, repeat the last frame, interpolate extra frames or truncate speech to hide a mismatch. A bad render fails validation and follows bounded retry/review policy. Audio may have silence appended to match valid video duration.

### 16.3 Overlong panels and semantic segments

A panel whose measured content exceeds the profile maximum needs a reviewed segmentation plan. It is not an automatic truncation case. Propose cuts at utterance/sentence boundaries; each segment retains the original immutable panel ID and gets a stable segment UUID plus explicit sequence position. Persist the approved plan as an immutable artifact. A reordering edit does not rename segment IDs.

Segment plans map line IDs and finite events to segments. No line is dropped or duplicated. Events crossing a cut need an explicit split/continuation rule. Reuse line-level TTS and the approved keyframe when suitable; render each segment independently. A single utterance longer than the maximum is a `duration_review` blocker requiring approved text/speech segmentation or an explicitly changed qualified duration profile. Do not alter speech speed or wording silently.

Add typed segment scopes to work and current-output keys; include the parent panel/series foreign keys. This is a small within-panel sequence, not a general nonlinear editor. Short panels have one segment through the same path. Chapter inclusion is once per panel, with its approved segment sequence expanded deterministically.

### 16.4 Render outputs

Submit only the qualified video path with actual source/reference, seed, steps, fps and frame count bound. Preserve remote output metadata and source attempt identity. Download only declared output artifacts; reject path traversal and unexpected types. If the chosen workflow produces its own soundtrack, explicitly discard it before final mixing. The approved TTS/local event mix is authoritative. Keep the original downloaded file as immutable evidence where useful; downstream clean footage has an explicit transformation manifest.

## 17. Local TTS and measured content timelines

### 17.1 First supported backend

Implement GPT-SoVITS first through a typed adapter around the actual installed local API. Qualify the required reference audio/transcript/language and synthesis parameters. Do not add multiple incomplete voice engines. No cloud fallback and no silent switch to a different voice or engine.

Each spoken line is an independently reusable work item keyed by stable line ID, normalized spoken text, exact approved voice/reference revision, language, engine identity and synthesis settings. Preserve display/source text separately. Persist each successful result immediately, so another line's failure does not discard it. Requests follow the same uncertainty policy when provider acceptance can be ambiguous; a provider without reconciliation cannot promise duplicate-free automatic retry.

Validate a decodable, finite, nonempty waveform. Record sample rate, channels, sample count and duration from decoded audio, not response text. A nonempty waveform is not proof of intelligible speech; the hardware acceptance includes listening. Store source output and a normalized lossless interchange artifact. Use 48 kHz stereo PCM for mixing, with explicit deterministic resampling/channel conversion. Preserve hashes and measured metadata for both.

### 17.2 Timeline representation

All internal audio timestamps are integer samples at the mix sample rate. Use rational conversions only at media boundaries. Avoid floating-point accumulation across lines/panels/chapters.

Dialogue is sequential by default, with a configurable initial inter-line gap of 120 ms, converted once to samples. This is an implementation default to calibrate, not an owner-mandated pacing value. Explicit overlap requires a reviewed cue. Leading/trailing silence and event tails are finite settings in the timeline manifest. A panel with explicitly approved no speech receives a configurable initial minimum of two seconds, extended by finite events; do not interpret failed/missing TTS as intentional silence.

Resolve symbolic anchors such as panel start, line start/end and relative offsets after measuring speech. Validate referenced line IDs, cycles, negative positions and asset availability. Store the resolved start/end samples and why each event is present. SFX and ambience reference local approved/selected assets with hashes, gain and finite trim/loop rules. BGM looping does not make a timeline infinite and does not extend panel length unless a finite intentional cue says so.

Produce a content-timeline artifact before render planning. It includes all line audio dependencies, event assets, segment assignment, sample positions, finite content end and gap/tail settings. An audio change invalidates this artifact and downstream frame planning only when required duration or another relevant dependency changes; valid line synthesis is retained.

### 17.3 Inputs the owner must supply

The implementation can provide interfaces and validation, but cannot fabricate approved character sheets, licensed/local sound assets, voice reference recordings, transcripts or the final pacing preference. Missing assets appear in production review as concrete required inputs. Permit an explicit selection of no BGM/SFX/ambience; missing dialogue/required translations are never silently optional.

## 18. Mixing and subtitles

### 18.1 Audio mixer

Implement the actual mixer in `audio.py`; do not leave a second diverging implementation in `av.py`. Build argv arrays for subprocesses and use controlled temporary paths. Avoid shell interpolation of captions, media paths or filter expressions.

Construct dialogue, SFX and ambience buses from sample-resolved cues. Apply configured source gains and validate finite output. Apply BGM resting gain 0.25 before speech-driven ducking. Do not normalize or boost the BGM bus afterward, which would undo that requirement. Use a documented speech sidechain and bounded limiter on the final mix if needed; avoid an automatic loudness operation that secretly changes requested relationships. Calibrate intelligibility and ducking on real audio during qualification.

Once the video plan is realized and validated, compute its exact rational duration and the corresponding ceiling sample count. Pad the audio with silence to that extent, accounting for codec padding metadata at final validation. Reject content that exceeds video duration. Never use `-shortest` to conceal missing frames or speech truncation. Save a lossless mix artifact before delivery encoding.

Test the real filtergraph with generated tones/silence and synthetic speech-envelope audio. Measure intervals with and without speech to show ducking, verify event placement and verify that resting BGM gain is not re-amplified. Assertions need tolerances appropriate to the codec/filter; avoid exact compressed-byte comparisons.

### 18.2 Subtitle artifacts

Generate cues from line IDs and the measured line timing; translations map to the same speech intervals. Required EN/ZH entries must be complete before production assembly. Empty translations for speech need an explicit approved exclusion; a returned empty string is not success. Preserve speaker labels/style options separately from spoken content.

Emit versioned ASS and SRT sidecars, with language metadata, fonts/style configuration and exact source line mappings. Escape ASS overrides, newlines and SRT syntax deliberately; never concatenate untrusted text into a shell command. Apply wrapping and safe areas for each ratio independently. Check Japanese/Chinese/Latin glyph availability using the chosen local fonts and record font identity/provenance.

Default delivery burns English and muxes Simplified Chinese as an MP4-compatible selectable subtitle track, normally `mov_text`; ASS remains a sidecar. Do not claim that arbitrary ASS styling survives conversion to MP4 soft subtitles. Include language/title/default-disposition metadata and validate it with ffprobe.

Prefer qualified libass rendering. If the target ffmpeg lacks libass, either provide a tested Pillow overlay backend with correct cue timing/glyph layout and equivalent configuration, or block with installation instructions. The first release may choose the blocker rather than ship an untested fallback. Missing glyphs or a missing requested renderer must not silently produce blank subtitles.

## 19. Video validation, upscale, encoding and assembly

### 19.1 Technical versus visual validation

Use ffprobe and actual decode/frame enumeration to check container readability, expected streams, dimensions, rational fps, frame count, duration and corrupt/truncated media. Existence and file extension are insufficient. Validate before promoting artifacts. A timeout/error report is not a successful output even if a partial file exists.

Visual-quality checks may compare structure/reference fidelity using an explicitly qualified local method or human review. They must not reintroduce age/content scoring. Do not fail intentionally dark/still manga solely because a generic black-frame or motion heuristic says so. No uncalibrated score may cause unlimited regeneration. Technical invalidity has bounded retries; subjective quality problems become visible review decisions.

### 19.2 Upscale

Implement and qualify Real-CUGAN as the default final-resolution path. Use the installed backend's real API/CLI contract. Preserve decoded frame order and count, use lossless intermediate frames if required, and carry fps as metadata. Where the backend scales only by integer factors, upscale and then uniformly resize the complete frame to the final target dimensions. Do not crop away source content. Reject mismatched aspect ratios instead of stretching.

Keep upscale independently reusable per video segment/ratio. It must not regenerate motion or TTS when retried. Account for temporary disk use and fail clearly before exhausting disk where size is predictable. Cleanup only known attempt-local intermediates after durable promotion; no recursive deletion of shared artifacts. If an explicitly selected native preview skips upscale, label it preview and keep it out of final-production pointers.

### 19.3 Central encoder

`encode.py` owns encoder selection, argv generation, capability probes and delivery settings. Default macOS production encoding is qualified `h264_videotoolbox`, H.264/yuv420p, faststart MP4, 48 kHz stereo AAC. Keep intermediate audio lossless. Linux development can select `libx264` explicitly. Do not silently fall back in production when the selected encoder fails; make the chosen encoder/settings part of provenance and expose a deliberate fallback selection.

Validate color/pixel-format behavior with the target pipeline; do not invent HDR claims. Subtitles are applied after upscale. Prefer a single final delivery encode from validated clean frames/mix where practical; avoid needless repeated lossy encodes. Probe required ffmpeg/ffprobe filters, encoders and muxers before expensive generation begins.

### 19.4 Chapter assembly

Build the ordered assembly manifest from approved database order and exact accepted segment artifacts. Never glob directories and assume filename order. Require each current production segment for every selected ratio, complete measured audio and required subtitle data. Reject preview/stale/missing dependencies.

Use hard cuts. Compute cumulative offsets using integer frames/rational fps and integer samples, not independently rounded decimal seconds. Offset subtitle cues by the same boundaries. Validate no missing/duplicated line or panel segments, then validate the final decode, stream metadata, total frame count and audio coverage. Store ordered artifact IDs/hashes and settings in a delivery manifest. Promote each valid ratio atomically; aggregate completion waits for all required ratios.

### 19.5 Chapter merge

Merge is an explicit operation over an explicit chapter order and ratio. Check compatible codec, dimensions, fps/time base, pixel format, audio format and relevant codec configuration before stream-copy concatenation. If incompatible, fail with an actionable option for an explicitly requested common-format re-encode. Do not silently transcode or omit a chapter.

Rebuild/offset soft subtitles and sidecars from source manifests rather than assuming container concatenation preserves cue semantics. Record source delivery hashes and merge order. A merged output is optional and does not gate individual chapter completion unless it is part of the requested run.

## 20. End-to-end orchestration

### 20.1 Deterministic dependency planning

`application/planning.py` derives desired work from selected chapters, revisions, requested mode and frozen run settings. It performs no model loads, uploads or submissions. `application/run.py` repeatedly plans, claims ready work under the owner epoch, executes through adapters, reconciles outcomes and derives progress. Stage-specific CLI commands use this same machinery with a narrower target; do not maintain a second queue/recovery path.

The effective dependency order is:

1. Import/detect → approved order → caption/OCR/motion/required translations and registry proposals.
2. Source/palette approval → reference sheet draft → exact reference/voice/speaker/asset approval.
3. Approved line text and voice → reusable line TTS → measured finite content timeline → reviewed segmentation if required → frame plan.
4. Approved crop/reference → shared color master → ratio keyframe.
5. Frame plan plus keyframe → video attempt → technical validation → upscale/clean footage.
6. Actual validated video duration plus measured timeline → padded final mix; translations plus line timings → subtitles.
7. Ordered validated footage/mix/subtitles → ratio delivery → aggregate chapter completion → optional merge.

Steps 3 and 4 are logical independent branches, but heavy GPU inference remains serialized. There is no circular dependency between final mix and render: **measured finite content determines the frame plan; validated actual video determines only final silence padding and delivery alignment.**

Batch ready work by resource lane when it reduces model reloads, subject to dependencies and fair chapter progress. Start with concurrency one for heavy inference and bounded CPU/download tasks. Do not implement a throughput optimizer before correctness. Avoid holding the GPU while waiting for human review or unavailable local assets.

### 20.2 Review and completion

When human review is needed, persist the exact pending evidence, release owned model resources, mark `waiting_review`, print/open the review location according to CLI settings and exit ownership cleanly. The owner completes review, then runs/resumes explicitly. Do not add a hidden background daemon that wakes itself after a web click.

Derive completion from current desired outputs, not the highest historical panel enum or whether a job ever existed. A run with a requested subset of ratios may finish that requested scope, but status must separately show whether the chapter satisfies all series-required ratios. Adding a ratio creates only the missing ratio branch and downstream deliveries. Removed ratios remain historical and stop gating completion only after an explicit settings change.

A service outage gives bounded reconnect/reconcile behavior and an actionable status. No hot polling loop, infinite retry or CPU-offload fallback to make apparent progress. Ctrl-C follows the documented drain/stop behavior; a second force request must report that in-flight computation may restart and must retain submission uncertainty where applicable.

A resumed run keeps its frozen configuration, but must revalidate source/approval dependencies against current domain revisions before promotion. Previously complete run manifests remain historical evidence. `run_work` records exact required fingerprints/artifacts for that run; the user-facing current-output pointer represents the currently selected desired configuration, not a guarantee that every historical run can share one pointer. A different run can reuse a validated artifact by exact fingerprint without regenerating it. Do not let executing an old run silently roll current series settings or current outputs back; show a conflict and require a new run/explicit selection when the desired configuration changed.

### 20.3 Operational surfaces

Status must answer: what is complete, what is currently owned/running, what blocks progress, which service/resource state is known, what can safely retry, and which exact command/review action resolves each blocker. Report counts per stage/ratio and distinguish reused outputs from new attempts. Logs carry run/work/attempt IDs without credentials or full private prompts by default. Detailed local manifests retain the provenance needed for investigation.

## 21. Ordered implementation tickets

The tickets below are the delivery sequence, not independent invitations to rewrite subsystems. P07 can build registry/review behavior offline; complete P08 qualification plumbing before finishing P07 generated-sheet execution, then proceed to P09. This is an explicit dependency, not permission to bypass profile qualification. Each ticket must leave imports, CLI help and its supported paths coherent. Do not present an unavailable path as working while another ticket is pending. Sizes are relative complexity, not calendar estimates. Hardware-gated tickets can be prepared offline, but their qualification gate cannot be waived.

### P00 — Establish the baseline and durable test ownership — medium

**Cleanup status:** Feature reorganization is complete: 145 offline cases pass (the previous 144 cases plus one split of an unrelated combined test), with all original assertion ASTs preserved. Source runtime behavior remains experimental. Recheck the suite in the implementation environment; future real-media/hardware test markers and gates remain part of the relevant feature tickets.

**Depends on:** no implementation ticket. **Primary files:** `tests/`, `pyproject.toml`, maintained developer docs.

- Verify upstream HEAD and applicable instructions. Run the existing suite once and record actual environmental failures separately from code failures.
- The documentation cleanup split review/phase-number tests into feature suites (§22) and shared helpers. Preserve those assertions and verify the suite before architectural changes; do not recreate the old test modules.
- Separate pure unit, real local media integration and opt-in hardware tests. Block accidental outbound generation/network in default tests.
- Keep obsolete scoring expectations and stale documentation out of the suite; keep local-only and script-checkpoint regressions. Do not delete a regression just because it identifies a still-open feature.

**Acceptance:** same useful behavior coverage before/after reorganization; test collection is deterministic; no review-specific test module remains; no live endpoint needed for the default suite. **Do not:** introduce a huge test framework or rewrite assertions solely to increase test count.

### P01 — Transactional metadata and domain identities — large

**Depends on:** P00. **Primary files:** `models/domain.py`, new `models/work.py`, `storage/database.py`, `storage/series_store.py`, `storage/__init__.py`.

- Implement §5 schema, constraints, typed payloads, migrations and transaction/CAS primitives.
- Add a `run_work` join table with `(run_id, work_id)` uniqueness so reusable logical work can belong to multiple runs without duplicating it. Add typed line/segment scope validation for TTS/render work; segment plans remain immutable artifacts.
- Implement stable page/panel identities and current-output/dependency queries. Introduce compatibility read views only where needed while porting callers; no writable parallel JSON store.
- Refuse nonempty unrecognized legacy data. Add first-install/setup and data-directory checks. Make the public store helper construct the configured store correctly.

**Acceptance:** two connections cannot lose updates; CAS conflicts are explicit; FK/unique constraints reject cross-series/duplicate identities; interrupted transactions roll back; database reopen preserves committed state; supported schema upgrades preserve data. **Do not:** build a historical-output migration tool or distributed leases.

### P02 — Command ownership and control transport — medium/large

**Depends on:** P01. **Primary files:** new `scheduler/ownership.py`, application context, CLI entry points.

- Implement the host-user OS lock across data roots, atomic owner discovery metadata and owner epochs.
- Classify read-only, mutating/executing and control commands. Require the owner for every metadata mutation except authenticated targeted control requests.
- Implement owner-scoped approval/takeover, identity verification, graceful stop request and stale metadata recovery. Force termination is a separate explicit action.
- Add the control request/acknowledgment protocol. At this stage a resource release operation without an implemented backend must report unsupported/pending, never success.

**Acceptance:** two CLI processes cannot both own execution; status/control work during ownership; stale PID reuse cannot cause termination; no generic `--yes` kills an owner; takeover waits for real lock release. **Do not:** start a background scheduler service.

### P03 — Append ingest and immutable order review — medium/large

**Depends on:** P01–P02. **Primary files:** `steps/ingest.py`, `review/sheets.py`, CLI import/review handlers.

- Port import writes to transactions, preserve existing pages, implement source identities and changed-source conflict handling.
- Use fresh PDF raster staging and numeric page ordering; preserve confidence/direction. Test chapters 1 and 100 with overlapping local page numbers.
- Implement full desired-order submission with expected revision and operation ID. Preserve every crop/panel ID through arbitrary swaps.
- Add explicit replacement/removal operations with affected-output summaries, tombstones and invalidation. Keep boundary editing deferred.

**Acceptance:** unchanged reimport creates no new pages/work; append keeps all old data; repeated PDF import cannot retain removed raster pages; two-tab stale review conflicts; multiple swaps survive save/reload; only intended chapter changes. **Do not:** use content hash alone to collapse legitimate repeated pages.

### P04 — Immutable artifacts, work/attempt recovery and ratio progress — large

**Depends on:** P01–P03. **Primary files:** `storage/artifacts.py`, `scheduler/queue.py`, `scheduler/monitor.py`, `gpu/worker.py`, stage reconcilers.

- Implement desired work, run associations, attempt transitions and exact active-attempt uniqueness.
- Move request intent/seed/upload names/output prefix before submission. Implement explicit acceptance persistence and uncertainty reconciliation.
- Implement staging/validation/rename/promotion protocol, stale-output fencing and current-output dependency checks.
- Replace every independent stage reconciler with the shared outcome path. Count downloaded/completed work only after successful validated promotion.
- Derive per-ratio progress and fresh retries from desired fingerprints. Ensure planners are pure and respect chapter/ratio selection before any side effect.

**Acceptance:** pass every applicable crash row in §9.5 and §23; known accepted work retries downloads without regeneration; ambiguous POST never blindly repeats; stale results remain historical; adding one ratio reuses shared/other-ratio outputs. **Do not:** claim remote exactly-once semantics.

### P05 — Resource lanes and real pause/resume — large

**Depends on:** P02, P04. **Primary files:** `gpu/lanes.py`, `gpu/worker.py`, application run loop, control handlers.

- Replace fixed ports/process-name killing with configured managed-service identities and typed start/stop/unload/probe operations.
- Route text/vision, TTS, ComfyUI and upscale through one resource manager. Stop new claims on pause; implement drain, safe immediate cancellation, release observation and resume.
- Preserve paused intent across process/network failure. Recover orphan-owned remote attempts before new work.
- Respect unrelated external GPU work; do not unload/interrupt another application's jobs or claim exclusive physical GPU ownership from a CLI lock.

**Acceptance offline:** deterministic fake-provider tests prove transition/control semantics. **Acceptance hardware:** drain and pause release Koma's resources to the accepted baseline, and resume reuses checkpoints. Release remains unqualified until measured. **Do not:** equate open ports with VRAM residency or use global interrupt if unrelated jobs can be affected.

### P06 — Typed script and line-level checkpoints — large

**Depends on:** P03–P04. **Primary files:** `steps/script.py`, domain/result models, local clients.

- Port recent checkpoint/fingerprint fixes without regression. Store each successful substep result durably before advancing.
- Introduce stable line IDs, typed OCR/speech/translation records, provisional speaker identities and symbolic event anchors.
- Validate complete required translations by line/language. Reuse unaffected caption/OCR/motion/translation results after failures or edits.
- Normalize malformed/empty local model responses into bounded typed errors. Close every client; fix attachment placement relative to the actual last user message; handle numeric/date Retry-After without unbounded waits; preserve endpoint-specific credentials and local-only routing.

**Acceptance:** partial translations survive crash; only missing/stale substeps retry; zero dialogue is explicit; malformed responses cannot mark completion; public/proxy/redirect routing fails before data dispatch. **Do not:** require approved speakers before discovery can create proposals, or silently assign the first character.

### P07 — Registry, references and production review — large

**Depends on:** P03, P06; execution of draft generation also uses P08 qualification. **Primary files:** `steps/cards.py`, registry models/store APIs, review templates.

- Persist canonical candidate/image pairing, source evidence and immutable character/variant/voice revisions.
- Build source/palette approval → generated/imported sheet → exact sheet approval without a dependency cycle.
- Bind approved versions to panels/lines, implement explicit preview namespaces and review conflict handling.
- Add playable voice references, speaker assignment, asset selection and required-input blockers.

**Acceptance:** rejected/unapproved revisions cannot enter production; exact approved variants reach compiled colorize requests; revised sheets invalidate only descendants; preview cannot accidentally complete production; no missing voice is replaced silently. **Do not:** turn proposals into approvals automatically.

### P08 — Profiles, pins and installed capability validation — large

**Depends on:** P04. **Primary files:** `adapters/registry.py`, `adapters/ui2api.py`, profile/component config, `models`/doctor CLI.

- Implement §15 typed roles, required slot/output validation, component compilation and authoritative pins.
- Preserve correct custom configuration-directory resolution and express graph inputs as typed paths that support literal dotted keys; do not overload dot-splitting strings ambiguously.
- Add qualification records, deterministic compile fixtures and setup diagnostics. Doctor returns nonzero for required capability failures.
- Provide bounded probe commands and sanitized evidence capture. Do not hard-code unverified runtime node assumptions.

**Acceptance:** stock/fused graphs bind actual seed/dimensions/source/steps; invalid nodes/slots/components fail before upload; custom config paths work; pin selection shown by status equals effective compile input. **Hardware gate:** installed workflow/component identity is verified before P09 production probes.

### P09 — Correct image workflows and first real video probe — large + hardware

**Depends on:** P05, P07, P08. **Primary files:** colorize/keyframe/render steps and workflow JSON.

- Implement shared color master and full-source fit/pad/mask processing.
- Repair the active outpaint graph using actual installed schema; verify both ratios and effective prompt conditioning.
- Probe one short generated clip using a temporary explicit measured-duration fixture, without pretending that this replaces TTS integration.
- Decode outputs and record actual frame/fps/source preservation evidence. Fix the smallest relevant workflow/adapter defect when a probe fails.

**Acceptance:** edge-marker preprocessing tests plus owner-reviewed live source/keyframe comparison in both ratios; actual video decodes at planned dimensions/grid/fps. **Do not:** expand into alternate model families while the selected path is unqualified.

### P10 — TTS, timelines and reviewed segmentation — large + hardware

**Depends on:** P05–P08. **Primary files:** new `adapters/tts.py`, `audio.py`, script/timeline models, review.

- Implement qualified local GPT-SoVITS synthesis, line-level reuse, waveform validation and PCM normalization.
- Resolve measured timelines and finite local event assets; add exact frame planning from sample counts.
- Implement approved within-panel segmentation and overlong-single-line review blockers.

**Acceptance offline:** synthetic waveforms prove sample arithmetic, no lost/duplicated lines and minimal invalidation. **Hardware:** actual selected voice is intelligible, stable enough for approval and measured duration drives rendering. **Do not:** use token estimates as final audio length or silently change speech rate.

### P11 — Mix and subtitle artifacts — medium/large

**Depends on:** P10. **Primary files:** `audio.py`, subtitle module if warranted, maintained local-media fixtures.

- Build real audio buses/ducking, sample-exact event placement and final silence padding.
- Generate EN/ZH timed ASS/SRT; validate glyphs, escaping, wrapping and ratio-safe layout.
- Implement and probe the selected subtitle burn path and MP4 soft-track muxing.

**Acceptance:** real ffmpeg tests verify durations/events/ducking; unicode and special-character cues render; no BGM re-boost; missing translation/font/renderer blocks cleanly. **Do not:** use mocked subprocess success as media evidence.

### P12 — Validation, upscale, encoding, chapter/merge outputs — large + hardware

**Depends on:** P09–P11. **Primary files:** `video.py`, `encode.py`, assembly application service.

- Validate decoded videos, implement qualified Real-CUGAN and centralized encoder settings.
- Build chapter assembly from exact manifests and approved order, with subtitle/audio offsets.
- Add explicit merge compatibility validation and requested re-encode path; promote final artifacts safely.

**Acceptance offline/local:** tiny synthetic clips produce decodable two-ratio chapter outputs with correct streams/frame count/subtitle timing. **Hardware:** target Mac encoder and GPU upscale qualify on generated clips; final outputs are watched/listened to. **Do not:** pad/time-stretch video, glob source files, or silently transcode incompatible merges.

### P13 — Unified run command, progress and operator workflow — large

**Depends on:** P03–P12 implemented; qualified production paths are required for real completion. **Primary files:** `application/context.py`, `planning.py`, `run.py`, `cli.py`, status/review surfaces.

- Implement the DAG in §20 and route stage commands through it.
- Stop/release at review, enforce selected scope, reuse exact valid outputs, expose blocker/recovery commands and stable JSON output.
- Integrate pause/stop/takeover across CPU, TTS, LLM, download and ComfyUI phases. Preserve bounded service-outage behavior.
- Remove obsolete enum shortcuts, duplicate reconcilers and compatibility writes once no callers require them.

**Acceptance:** synthetic end-to-end flow, review/resume, missing-ratio addition, one-line translation/TTS retry and interruption recovery all converge to truthful completion without duplicate submissions. **Do not:** report “fully working” if hardware gates are pending.

### P14 — Operational qualification and release cleanup — medium + owner hardware

**Depends on:** P00–P13. **Primary files:** maintained README/config/setup/operator docs, final feature tests.

- Execute §§24–25, resolve failures in the responsible feature suite and record runtime evidence outside git.
- Reconcile README/PLAN/DECISIONS statements with settled owner decisions; clearly mark legacy research/profiles experimental rather than treating them as current proof.
- Verify setup diagnostics, temporary disk cleanup, signal behavior, log redaction and incomplete-work recovery instructions.
- Inspect tracked files and every pending commit for accidental owner media, reports, test outputs, caches or model assets.

**Acceptance:** all release gates pass, or the result explicitly lists the remaining hardware/input blockers and does not claim readiness. Commit only coherent production changes and enduring tests/docs. No arbitrary coverage percentage or historical test-count target substitutes for behavior evidence.

## 22. Long-term test organization

Organize tests by maintained behavior, not the review that discovered it. Suggested layout:

| Suite | Scope |
|---|---|
| `test_storage.py` / `test_artifacts.py` | Transactions, constraints, migrations, CAS, immutable promotion and dependencies. |
| `test_ingest.py` / `test_review.py` | Import identity, PDFs, stable panel order, approvals and stale browser submissions. |
| `test_script.py` / `test_local_generation.py` | Typed substeps, translation completeness, checkpoint reuse, endpoint/client restrictions. |
| `test_registry.py` | Character/variant/voice versions, canonical evidence, production/preview bindings. |
| `test_scheduler.py` / `test_recovery.py` | Work/attempt state transitions, uncertainty, ratio progress, stale completions. |
| `test_ownership.py` / `test_resource_lanes.py` | Separate-process exclusion, controls, epochs, safe takeover and release observations. |
| `test_profiles.py` / `test_workflow_conversion.py` | Role/slot/component validation, pin authority, graph/schema fixtures and cycle handling. |
| `test_timing.py` / `test_audio.py` / `test_subtitles.py` | Sample/frame arithmetic, cues, synthesis reuse, real mix and subtitle rendering. |
| `test_video.py` / `test_encode.py` / `test_run.py` | Decode validation, upscale contracts, encoding/assembly/merge and application integration. |

Split a suite only when its size warrants it. Use reusable fake providers with explicit acceptance/history/cancellation behaviors; do not mock every internal method so the test merely repeats the implementation. Use a fake clock for retry/control timing. Real ownership tests use subprocesses and synchronization primitives, not timing-sensitive sleeps.

Default tests cannot reach owner services or the public internet. Small generated images/audio/video are created in temporary directories. Commit tiny fixtures only when they preserve an externally meaningful format/schema case that cannot be generated clearly. No large generated media or raw private model responses.

Mark real ffmpeg integration tests separately and require them in an environment with ffmpeg before merging media features. Mark hardware tests opt-in with explicit local service settings. A skipped hardware test is a skipped qualification, not a pass. Document exact commands in maintained developer docs; use the repository's existing tooling rather than inventing a second build system.

## 23. Required failure and regression coverage

Each row is a durable behavior test or qualification scenario, not a new review-specific test file.

| Area | Injected condition | Required observation |
|---|---|---|
| Storage | Two writers read the same revision | One succeeds; the other gets a conflict; no overwritten unrelated fields. |
| Import | Same source reimport; new page; changed existing bytes | Respectively no-op; append; explicit replacement blocker. |
| Import | Identical bytes at different intended occurrences | Preserve both occurrences and stable identities. |
| Review | Several swaps, reload, stale second tab, duplicate operation | Desired order persists; identities/crops stay aligned; stale writes reject; duplicate operation is idempotent. |
| Scope | Chapter 100, same local page IDs in two chapters, invalid ratio | Correct ownership and selection; invalid ratio rejected before work. |
| Queue | Two claims, failed attempt, changed fingerprint | One active attempt; definite failures can retry; new desired revision is not suppressed by history. |
| Ratios | First ratio succeeds, second fails, third setting added later | Aggregate remains incomplete; retry only missing branch; accepted other work reused. |
| Dispatch | Crash before POST; accepted POST loses response; response before DB commit | Persisted prepared intent is safe to resume before dispatch; possible dispatch reconciles/holds; no blind duplicate. |
| Recovery | History cleared/server restarts; multiple matching prompts | No false proof of nonacceptance; ambiguous cases hold with evidence. |
| Download | Network fails halfway or after remote success | Resume/retry same accepted attempt; no partial current artifact. |
| Promotion | New source revision while old attempt finishes | Old result retained historically; current pointer cannot regress. |
| Promotion | Crash after rename but before DB commit | Valid orphan can be reconciled; old accepted pointer remains intact. |
| Ownership | Competing data roots, stale metadata, PID reuse | One host-user owner; no unauthorized process termination. |
| Controls | Pause during LLM, TTS, queue, active render, download | No new inference admission; correct checkpoint/drain; truthful resource status. |
| External GPU | Unrelated job shares service/device | No global interrupt/unload that disrupts it; actionable release blocker if safe release is impossible. |
| Script | Missing one translation; malformed result; crash after substep | Only missing/stale substep retries; valid partial artifacts reused; completeness remains false. |
| References | Rejected sheet; changed approved revision; explicit preview | Production blocked or invalidated appropriately; preview stays separate. |
| Local routing | Public URL, redirect, proxy environment, wrong credential scope | Rejected/no data dispatch; local credentials isolated. |
| Profiles | Bad pin/custom config path/missing weights/wrong output socket | Preflight failure with actual effective profile; no fallback masking damage. |
| Outpaint | Distinct markers at all source edges | Fit preserves full rectangle and effective graph binds it for both ratios. |
| Timing | Audio just beyond frame boundary/grid maximum | Ceiling/grid respected; reviewed segmentation or clear blocker; no truncation. |
| Audio | Speech + resting/ducked BGM + late SFX | Events correctly placed; gain/ducking measurable; finite tail included. |
| Media | Wrong frames/fps/dimensions, corrupt last frame, extra soundtrack | Reject or explicit clean transform; no falsely completed delivery. |
| Subtitles | CJK, quotes/braces/backslashes, missing font, long portrait cue | Escaped readable output or explicit blocker; correct language tracks. |
| Assembly | Stale/preview segment, wrong ordering, incompatible merge | Reject invalid selection; manifest order wins; explicit re-encode decision. |
| Resume | Restart after each accepted pipeline stage | Reuse durable checkpoints; no whole-chapter restart required. |

Also preserve fixed baseline regressions for final-job drain, chapter parsing, converter cycles, public store helper and required render injections. The table supplements the detailed crash matrix; neither may be reduced to a single happy-path integration test.

## 24. Hardware qualification procedure

### 24.1 Prerequisites and evidence

The implementation agent should complete all offline work possible before requesting owner-machine access or assets. Request only concrete missing inputs: installed provider/component identities, accessible local endpoints/service control, approved references/voice samples/local assets, and the owner's acceptable throughput/resource-release expectations. These are operational inputs, not reasons to reopen the settled product decisions.

Run qualification on the actual Mac orchestration and Windows GPU topology. Capture software/model identities, available RAM/VRAM/disk, service ownership, selected encoder/fonts and initial GPU memory. Do not treat old installation notes or reported VRAM estimates as measurements. Avoid collecting secrets, raw environment dumps or unrelated application data.

There is no defensible universal maximum generation time in this plan. Report time per stage and frame/second throughput alongside peak memory and CPU/offload behavior. Ask the owner to accept or adjust concrete measured presets. A setting that spends unreasonable time in CPU/RAM execution is a failed performance candidate, not an excuse for a cloud fallback.

### 24.2 Qualification ladder

1. **Doctor/preflight:** verify local-only routes, component bytes, installed node schema, service control, free disk, ffmpeg/encoder/subtitle capabilities. Fail cheaply before loading heavy models.
2. **Lane lifecycle:** start/use/unload each required service individually. Measure Koma-owned memory before/after and check that unrelated GPU processes survive. Establish a realistic release baseline and timeout; port closure alone is insufficient.
3. **Text/vision:** one source crop yields validated caption/OCR/EN/ZH translations and registry proposals, with successful checkpoint/retry of a deliberately missing translation.
4. **TTS:** approved voice renders representative Japanese lines, including punctuation and a long line. Listen, inspect waveform/sample counts and record latency. Test release/resume.
5. **Images:** approved source/ref produces color master and both-ratio keyframes. Compare full source visibility and reference fidelity manually; record actual graph identity.
6. **Motion:** shortest useful qualified clip in each ratio; decode all frames and compare actual grid/fps/duration. Confirm unwanted generated audio is removed from downstream footage.
7. **Upscale/encode/subtitles:** generated clip reaches required resolution, retains frame count and source content, renders readable EN, offers selectable ZH and decodes on the Mac's normal player.
8. **Synthetic end-to-end:** two distinguishable panels/two ratios with measured speech/events, hard-cut order, correct subtitles and complete manifests.
9. **Small real chapter:** normal two review checkpoints, approved assets and a complete watched/listened deliverable for each ratio.
10. **Recovery exercises:** pause mid-work, drain/release, resume; terminate the orchestrator at a controlled point; interrupt service connectivity after submission; resolve uncertain acceptance without blind resend. Avoid destructive experiments on unrelated GPU work.
11. **Representative full chapter:** owner-selected size/content, progress reporting, temporary disk behavior and performance acceptance. Verify an unchanged rerun produces no generation requests.

Each step records pass/fail/unqualified with evidence. A failure should name the responsible profile/stage and smallest corrective change. Repeat only affected downstream qualification after a fix; do not waste GPU time rerunning unaffected stages indiscriminately.

## 25. Release gates and operator handoff

### 25.1 Readiness levels

| Level | What can honestly be claimed |
|---|---|
| Implemented | Production code exists and feature unit tests pass. |
| Locally integrated | Real local ffmpeg/media and synthetic application integration pass. |
| Hardware qualified | Selected actual local model/service/encoder paths passed bounded probes and lifecycle checks. |
| End-to-end accepted | Owner-reviewed chapter outputs and recovery scenarios pass for every required ratio. |

Only the last level supports “fully working video generator” for the selected supported configuration. No claim is made that every experimental profile or arbitrary hardware configuration works.

### 25.2 Required release checklist

- All required source panels and approved segment mappings are accounted for in both output manifests.
- Approved reference/voice versions, required translations and measured audio exist; no preview artifact satisfies production.
- Full-source keyframes, actual motion, required upscale/resolution, subtitle tracks and intended audio are validated.
- Repeated import/run is idempotent; adding a ratio or retrying one failed substep does the minimum necessary work.
- Unknown submissions hold/reconcile; late stale completions cannot overwrite newer outputs.
- One CLI owner is enforced, targeted controls work, and GPU release/resume is measured and truthful.
- No generation request can escape to a public service through a configured endpoint, redirect, proxy or fallback.
- Default tests, required local-media tests, lint and packaging/CLI smoke checks pass; hardware status is reported separately.
- Tracked repository content contains only production code, maintained tests/config/docs and intentionally small fixtures.

### 25.3 Operator documentation contract

Document a single tested walkthrough using the final CLI syntax: doctor → model verify/probe/pin → new series → append import → order review → run to production review → approve assets/bindings → resume to deliveries. Include status, drain pause, resume, stop, exact-owner takeover, uncertain-attempt inspection/resolution, explicit source replacement/removal, ratio addition and optional merge.

Every example must be executed against a disposable test data directory or tested CLI harness before publication. This plan's command names establish behavior; the implementing agent must choose consistent Typer argument spelling and update help/docs/tests together. Do not publish fictional commands or add aliases for every historical spelling.

Final implementation report should state commit hashes, implemented milestones, real validation performed, hardware qualification identity, exact remaining blockers and required owner action. Keep probe logs/media/results in runtime storage, not git. A hardware-blocked result must still deliver complete runnable offline implementation and a concrete qualification command/procedure, rather than stopping at an abstract recommendation.

## 26. Coverage of the original review and discarded work

“Preserve” means the baseline contains a targeted fix that must survive the architectural changes; it does not imply full hardware validation. “Complete” means this handoff includes remaining implementation or acceptance work.

| Finding | Treatment | Responsible sections/tickets |
|---|---|---|
| B01 Final running job drain | Preserve; integrate with durable outcomes and pause | 8–9, P04–P05 |
| B02 Lost page mutations | Complete with transactions/CAS | 5, P01/P03 |
| B03 Unsafe queue writes/claims | Replace writable JSON queue with transactional work/attempts | 5/9, P04 |
| B04 Unrecoverable submission boundary | Complete durable intent, reconcile/hold and crash tests | 9, P04 |
| B05 History suppresses retries | Complete work/attempt separation and fingerprints | 6/9, P04 |
| B06 One ratio completes panel | Complete independent outputs and derived aggregate state | 6/20, P04/P13 |
| B07 Order changes identity | Complete immutable IDs and revisioned ordering | 11–12, P03 |
| B08 Review loses visible swaps | Complete full desired permutation/idempotent save | 12, P03 |
| B09 Ambiguous page certification | Complete explicit series/chapter/page ownership | 5/12, P03 |
| B10 Incremental import drops pages | Complete append/no-op/explicit replace/remove | 11, P03 |
| B11 Missing default render profile | Preserve resolution fix; qualify selected profile | 15, P08–P09 |
| B12 Missing seed/ratio sizing | Preserve bindings; inspect effective graph and probe | 15–16, P08–P09 |
| B13 Fused IMAGE binding | Preserve structural fix; qualify actual installed graph | 15–16, P08–P09 |
| B14 Approved variants unused | Complete versioned bindings and reference consumers | 14/16, P07/P09 |
| B15 Conflicting profile pins | Complete one database authority | 15, P08 |
| B16 Jobs lack series identity | Complete owned foreign keys and scopes | 5/9, P01/P04 |
| B17 Late stale overwrite | Complete immutable paths and fenced promotion | 6, P04 |
| B18 Offline/transport handling | Extend existing hold to whole attempt/recovery lifecycle | 8–9, P04–P05 |
| B19 GPU arbitration | Extend partial handoff to owner/lane/resource lifecycle | 7–8, P02/P05 |
| B20 Source-destructive outpaint | Complete preprocessing/graph correction plus live proof | 16, P09 |
| B21 Missing translations complete | Preserve recent fix; extend to typed line/language completeness | 13, P06 |
| B22 Nonreusable script checkpoints | Preserve recent improvements; port to immutable transactional results | 5/13, P06 |
| B23 Timing rounds down | Preserve ceiling logic; replace estimates with measured sample/grid contract | 16–17, P10 |
| B24 Chapter 100 parsing | Preserve regression across new scope APIs | 11/23, P03 |
| B25 Chapter/ratio options ignored | Complete shared planner selection before side effects | 10/20, P04/P13 |
| B26 Candidate/image mismatch | Preserve pairing fix; persist canonical evidence and review revisions | 14, P07 |
| B27 Cloud guard/privacy conflict | Preserve removal/local-only enforcement; no replacement scoring project | 1/13/23, P06 |
| B28 False component verification | Preserve failure semantics; add identity/qualification distinction | 15, P08 |
| B29 Broken default store helper | Preserve public helper behavior on new store | 5, P01 |
| B30 Converter bypass cycle | Preserve cycle protection and expand stable format fixtures | 15, P08 |

Other concrete maintenance findings are folded into their feature tickets: client close/attachment/retry behavior in P06; output-slot selection and truthful download counts in P04/P08; custom config paths and literal dotted graph keys in P08; detector confidence/PDF ordering in P03; encoder/audio/video stubs and orchestration gaps in P10–P13; misleading setup/docs in P14.

The following are intentionally **dropped from the v1 backlog**, not silently forgotten:

- Automated age/content scoring, Civitai/WD scoring and cloud Llama Guard; the owner removed this requirement.
- Migration/repair of historical accepted outputs; none exist. Future schema migrations and safe refusal of unknown data remain required.
- Distributed/multiple-machine worker scheduling, overlapping heavy CLI execution, generic job-platform extraction and unrelated package splitting.
- A wholesale rewrite of working detector/client/compiler logic merely for stylistic consistency.
- Experimental ref2va/continuity engines, multiple alternate TTS/painter implementations and generative ambience before the selected v1 path is qualified.
- Boundary-editing UI, a general timeline editor, cloud fallback and automatic recurring orchestration.
- Speculative mandatory quality-score thresholds, arbitrary test-count targets, old throughput/VRAM promises and mandatory chapter sizes without measurements/owner acceptance.

## 27. Drift prevention and next-agent startup checklist

### 27.1 Invariants to check in every design review

1. SQLite is the only mutable metadata authority; exported JSON cannot write back implicitly.
2. Ordering and labels never redefine entity identity.
3. Existing import occurrences survive append; changed bytes do not silently replace them.
4. Every work item/attempt/artifact has validated ownership, mode and relevant revision identity.
5. Accepted artifacts are immutable; current pointers move only after validation and freshness checks.
6. Unknown remote acceptance is never equated with failure simply because history is empty.
7. Retrying transport/download does not automatically create a new inference attempt.
8. Every configured ratio gates aggregate completion; previews do not.
9. Approvals refer to exact immutable evidence and bindings, not whichever sheet currently has a convenient filename.
10. Script discovery can precede binding approval; production generation cannot bypass required approval.
11. Required translations and measured speech remain independently checkpointed.
12. Audio length determines frame planning; bad video is not padded or retimed to conceal defects.
13. Full source content reaches ratio-specific outpainting without a hidden crop.
14. BGM gain/ducking and subtitle language behavior remain explicit and testable.
15. One local command owns execution, while status and targeted controls remain usable.
16. Stopping a conflicting command requires scoped user approval; unrelated GPU applications are not collateral targets.
17. Paused/released status reflects observed state; inability to prove release stays visible.
18. Generation is local across every provider, including text/translation and optional preview.
19. Model existence, offline tests and live qualification are three different claims.
20. Commits contain enduring product code/tests/docs, not review reports or runtime evidence.

### 27.2 First actions for the implementing agent

1. Read the complete handoff and current repository instructions; verify `main` and inspect any changes since the pinned baseline.
2. Produce a short milestone checklist mapping already completed/new work to P00–P14. Resolve conflicts using the authority order in §0.
3. Execute P00, then P01; use the schema and behavior contracts as the architecture boundary. Do not begin by implementing a speculative media engine around the old queue.
4. Proceed in ticket order, allowing independent offline preparation of later adapters only after their contracts are fixed. Keep qualification status truthful.
5. Before each coherent commit, inspect the diff and tracked files, run the responsible durable tests and update maintained docs. Keep this maintained plan current; do not commit duplicate handoff reports.
6. When hardware/asset input becomes the actual blocker, present the exact runnable probe or review request and expected evidence. Continue other authorized offline work that does not depend on it.
7. Finish with the readiness level and concrete remaining blockers from §25, not a percentage-complete guess.

**Handoff rule:** When a detail is not specified, choose the smallest implementation that preserves these invariants, record the choice in maintained code/docs and test externally visible behavior. If the choice changes an owner decision or product output semantics, obtain a decision rather than silently drifting. Routine module names, helper signatures and test organization do not require reopening the product design.


