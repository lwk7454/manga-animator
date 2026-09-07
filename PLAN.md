# manga-animantor — Design & Build Plan

Status: **PLAN — not yet implemented** (plan-only phase, per Pooh's instruction)
Date: 2026-09-08
Decisions: locked via grilling session — see `DECISIONS.md` (authoritative log, vidgen convention)
Research: `docs/research/` (primary-source dossiers, Sep 2026)

---

## 1. Mission

Convert a locally-provided manga (JPEG/PDF, B&W or color, Japanese or Korean/
webtoon reading directions) into a coherent animated video per chapter —
"anime-fication": each colorized panel becomes a video keyframe, a
motion prompt drives real generated motion, characters keep stable identities
(colors **and** voices) across an indefinitely long series, with Japanese
voice acting, translated subtitles (English + Simplified Chinese priority),
and layered adult audio.

Hard constraints (from the brief + grilling):

- **No paid image/video generation services.** All pixels from the local
  3090 box (192.168.50.103) via ComfyUI. Cloud LLM tokens for
  writing/translation are allowed (pennies), but never image/video/TTS-as-a-service.
- **Adult content is a first-class feature** (hentai manga, adults only).
  Every model choice was vetted for non-refusal; see §4 and the research
  dossiers. ElevenLabs is **out** for this pipeline (policy + filter risk).
- **Mandatory age-gate before any generation** — fail-closed, OR-logic
  across two anime-specialized classifiers. Adults-only is enforced, not assumed.
- **The 3090 is a shared, intermittently available machine.** Long-running
  work must be resumable, non-blocking, and manually pausable at any moment
  (pause = drain, then hold). Machine-off ≡ paused.
- **Indefinitely long series**: append-only ingest, living character
  registry, one video per chapter as the deliverable.

---

## 2. What it is / isn't, relative to vidgen

`video-generator-v2` (aquaponics social videos) is the architectural base.
koma inherits its proven skeleton and rewrites the domain layer:

| vidgen concept | koma equivalent |
|---|---|
| Topic / content plan | Source manga (pages on disk) |
| Storyboard LLM output | Panel script (per-panel caption, dialogue, motion, timing) — **derived from the art, not invented** |
| Scene (narration unit) | Chapter (deliverable unit) |
| Shot (view unit) | **Panel** (one keyframe → one clip per ratio) |
| Keyframe (nbpro, fal.ai) | Colorized panel art, reframed per ratio (outpaint) |
| Voiceover (ElevenLabs) | Dialogue via local TTS (GPT-SoVITS / IndexTTS-2.5) |
| Single 9:16 output | 16:9 and/or 9:16, full generation per ratio |
| Project store (JSON) | Series store (series → chapters → pages → panels) |

Key architectural inheritances (do not re-litigate — proven in vidgen,
see skill `sympuro-video-generator`):

- Two-phase pipeline: fast sequential prep → async job monitor that survives
  crashes/reboots. Idempotent steps; artifacts on disk are the source of truth.
- Audio-first sizing: clip length = panel's audio duration, snapped onto the
  video node's frame grid (MiniMax H3: **17n+5** frames, live-read from
  `/object_info`, never hardcoded — vidgen decision #38).
- **Never pad or speed-adjust video.** Absorb surplus as audio-tail silence.
- Unique per-job output prefixes AND unique per-job input upload names
  (ComfyUI's input/output namespaces are shared across queued jobs —
  vidgen decisions #11/#40, both live-proven collision bugs).
- free_vram (`/free` unload_models) before Real-CUGAN upscaling.
- Encoder profile in ONE module (h264_videotoolbox, yuv420p, faststart).
- Storyboard-style guardrail validation with bounded LLM self-correction
  retries; validators must not punish correct behavior.
- Real-binary regression tests for every ffmpeg/shell-out call; live GPU
  probe after any workflow/node change; use REAL artifacts for live tests.
- `poll_clip` treats "nowhere on server" as FAILED (no zombie QUEUED);
  ComfyUI restart restores NOTHING from a crashed queue → mark lost
  jobs FAILED (clear prompt_id) → re-queue.

---

## 3. Pipeline overview

Three stages, two machines. **Mac does everything that looks and thinks;
the 3090 does everything that makes pixels.** No stage blocks for long: every
GPU operation is a small per-panel job (~70s warm for i2v) with state on
disk; the Mac-side monitor submits, polls, downloads, advances.

```
STAGE A — Mac (fast, no GPU)
  A1 ingest        pages → panel crops + reading order (detection + OCR heuristic)
  A2 order-review  dark-theme HTML contact sheet → human certifies (per batch)
  A3 age-gate      Civitai SigLIP2 + WD-tagger (+ optional Llama-Guard-4), OR-reject, fail-closed
  A4 script        per panel: caption (local vision), OCR text → dialogue lines,
                   speaker attribution, motion plan, translations (EN, zh-HANS)
  A5 cards         character discovery → character cards → human review gate
                   (colors + voice refs) → registry (living, versioned)

STAGE B — 3090 (queue-based, pausable)
  B1 colorize      MangaNinja ref-following colorization per panel (B&W only),
                   guided by character-card sheets; skipped for color sources
  B2 keyframe      per ratio: reframe panel (outpaint, Qwen-Image-Edit) → keyframe
  B3 i2v           MiniMax H3 fl2va turbo: keyframe + motion prompt → clip
                   (audio-first length, 17n+5 grid)
  B4 tts           dialogue/moans via local TTS service (GPT-SoVITS primary,
                   IndexTTS-2.5 emotion lane) — batched when video lane is idle
  B5 upscale       Real-CUGAN → 1080p (per-ratio toggle), free_vram first

STAGE C — Mac (fast)
  C1 mix           dialogue + moans/SFX + ambience + soft BGM (0.25 resting gain,
                   sidechain ducking — vidgen audio graph)
  C2 subs          ASS build (EN primary, zh-HANS secondary), Pillow burn fallback
                   (brew ffmpeg has no libass)
  C3 assemble      per-panel audio-lock concat → chapter video (per ratio)
  C4 merge         optional no-reencode concat of finished chapters (binge file)
```

Review gates (the only two human touchpoints per batch): **A2** order
certification, **A5** character cards. Everything else runs unattended.

---

## 4. Model stack (locked choices + rationale)

Full primary-source dossiers in `docs/research/`. Summary:

| Role | Model | Runs on | License | Why |
|---|---|---|---|---|
| Video (default lane) | **MiniMax H3 fl2va pruned-int8 + turbo LoRA** (ComfyUI core nodes) | 3090 | MiniMax license | Already live-proven in vidgen (~70s/clip warm). Local weights have **no content filter** (filter lives only in MiniMax's hosted API) — the "jailbreak version" is unnecessary; local IS unfiltered |
| Video (coherency/regen lane) | **MiniMax H3 ref2va** (same weights family, separate download) | 3090 | same | `MiniMaxH3ReferenceToVideo`: up to **9 ref images + 3 ref videos (≤15s) + 3 audios** per generation; + `MiniMaxH3AddGuide` pins a panel at frame 0 → "refs + start frame" in one job. Reserved for identity-critical panels and regens |
| Colorization | **MangaNinja** (`ComfyUI_MangaNinjia` pack) | 3090 | see pack | Reference-following line-art colorization — built exactly for "colorize this panel consistently with this character sheet" |
| Outpaint / keyframe reframe | **Qwen-Image-Edit** (+ turbo) | 3090 | Apache-2.0 | Strong instruction following, **native Japanese text rendering** (balloons that survive outpainting), 3090-friendly |
| Upscale | Real-CUGAN (existing) | 3090 | existing | Inherited from vidgen |
| Vision (captions, speaker ID, crop flags, palette proposals) | **Qwen3-VL-8B abliterated** (huihui-ai) | **Mac** (Ollama) | Apache-2.0 | Cloud vision models refuse explicit images; no uncensored vision model exists on any cloud router. ~6GB, JP-strong, abliterated to not refuse. Swappable via adapter spec |
| OCR | **manga-ocr-base** | Mac | Apache-2.0 | Purpose-built for manga: vertical text, furigana, overlaid text, low-quality scans |
| Text LLM (dialogue polish, translation EN/zh-HANS, motion-prompt writing) | **glm-5.3-flash** via Ollama Cloud | cloud | $0.15/$0.50 per M tok | JP-strong, cheapest vision-capable cloud model, Ollama Cloud ToS has no adult-fiction ban (dossier 3). Any OpenAI-compatible endpoint swappable |
| Age-gate | **Civitai SigLIP2 anime age classifier** + **WD tagger v3** (+ optional Llama-Guard-4 via OpenRouter $0.18) | Mac | per-model | SigLIP2: calibrated child P=0.995/R=0.936 (tune threshold down — ~16% of anime minors missed at default). WD v3 tag list literally contains loli/shota/child tags — orthogonal signal. **OR-reject, fail-closed, per page AND per chapter** |
| TTS primary | **GPT-SoVITS** | 3090 (HTTP service) | **MIT** | 5s zero-shot clone / 1min fine-tune, JP+EN, ~2–4GB VRAM, no text filter |
| TTS emotion lane (optional) | **IndexTTS-2.5** | 3090 (batched) | bilibili license (free under 100M MAU — fine) | Best emotion control: timbre–emotion disentanglement, text-described delivery ("say this breathy/urgent") — the whisper/moan lane |
| Moans/SFX | licensed packs (~$10, e.g. magicalmysticva packs) + Freesound CC | local files | per-pack | Deterministic, cheap, high quality; MMAudio (MIT) for synced room ambience later |
| BGM | small per-mood pool, soft | local files | TBD sourcing | Resting gain 0.25, sidechain duck (vidgen graph, inherited) |
| Video — *optional painter swap* | **PinkCherry** H3 fl2va finetune (§11.1) | 3090 | finetune of MiniMax-licensed weights — local private use only, never redistribute | Competence upgrade for explicit scenes (DiT actually trained on adult content); adopted only on A/B evidence |
| Encoder — *optional reader swap* | **Heretic** Qwen3-VL-32B abliterated (§11.1) | 3090 | Apache-2.0 (encoder) | Follows explicit motion prompts more literally; likely valid for both video lanes (probe decides); adopted only on A/B evidence |

**ElevenLabs: NOT used.** Written policy has a fiction carve-out, but the
automated pre-generation filter unpredictably blocks sexual text and
sustained NSFW use risks suspension (dossier 2). It stays available for
non-explicit narration in *vidgen*, not here.

Optional installs (not required for v1): PinkCherry NSFW finetune of H3 fl2va
(DiT finetune, drop-in), Heretic abliterated Qwen3-VL-32B encoder swap
(prompt-understanding side). Both are one `koma setup` + config line away via
the adapter registry. Popular "MiniMax-H3-NSFW" HF repos are **scam reuploads**
— never install them.

### VRAM budget (3090, 24GB — from dossier 1, smeltcore's 3090 Ti recipe)

| Phase | Resident | Fits? |
|---|---|---|
| Text encode (nvfp4 Qwen3-VL-32B encoder) | ~15.8 GiB | yes (never co-resident with DiT — ComfyUI dynamic offload swaps) |
| Denoise (pruned int8 DiT) | ~20.7–21.0 GiB | yes, ~3GiB spare |
| VAE decode (video fp16) | ~6.3 GiB | yes |
| Refs (ref2va) | +~1 frame latent each at `ref_image_size="match"`; "max"(2048px) is **several× slower** | keep refs at generation res, count low |
| TTS service | 2–4 GiB | co-residency tight during denoise → schedule TTS batches while video queue is drained/paused |

Local H3 is **768p canvas max** (1344×768), trained range **124–362 frames
(~5–15s @24fps)** — per-panel clips (typically 22–107 frames) sit comfortably
inside. koma's canvas presets: 832×480 (16:9) / 480×832 (9:16), same as vidgen.

---

## 5. Coherency strategy (the "does it look like one anime?" question)

Three layers, cheapest-first:

1. **The art itself (primary).** Every clip's first frame IS the artist's
   panel — character identity is inherited from the original art, which is
   more consistent than anything we can synthesize. Colorization is anchored
   to the same character sheets for every panel, so palette holds across the
   chapter/series.
2. **Panel-chain conditioning (per-scene continuity, cheap).** The stock i2v
   node accepts `first_frame` **and `last_frame`**: when consecutive panels
   share a scene (same characters, same setting — the script stage knows),
   clip N starts at panel N and ends at panel N+1's art. The artist already
   drew where the scene goes; we don't invent it. Frame grid still 17n+5;
   this uses the SAME fl2va weights (no extra download).
3. **ref2va reference conditioning (regen + identity-critical panels).**
   `MiniMaxH3ReferenceToVideo` + `AddGuide` (panel at frame 0) with up to 9
   identity refs (character sheets, canonical panels). Reserved lane —
   slower and ref-VRAM-hungry — for: regens after a registry fix, panels the
   vision validator flags as identity-drifted, and cross-chapter "same
   character, new chapter" locks. Refs downscaled to ~generation res
   (`"match"`), never `"max"`.

**Feeding the previous clip as ref video:** supported (≤3 videos, ≤15s) but
deliberately NOT the default — it's the expensive path (ref tokens ride
every sampling step) and it couples every clip to its predecessor's errors.
Panel-chain (layer 2) gets temporal coherency cheaper; ref-video stays in
the toolbox for the regen lane. This directly answers the brief's question:
yes it's supported, no we don't do it by default, and ref compression =
"match"-size refs + few of them (the 3090 headroom math is in §4).

Identity validation: after each clip downloads, the local vision model
compares clip frames against the character card (same check vidgen's
validate step does for content, extended with identity criteria). Fail →
panel marked NEEDS_REGEN → optional auto-regen via ref2va lane or a
regen-review list at the next natural checkpoint.

---

## 6. State model & storage

```
~/dev/manga-animantor/            # repo (see §10)
  data/                           # gitignored — ALL pipeline state lives here
    series/<slug>/
      source/                     # original pages (imported copies)
        ch01/p001.jpg ...
      series.json                 # series meta: reading direction, language, source type
      pages.json                  # page + panel index: crops, bboxes, order, review status
      registry/
        characters.json           # living character registry (§7)
        assets/                   # character sheets, voice refs (versioned)
      chapters/<NN>/
        script.json               # per-panel: caption, dialogue, motion, translations, timing
        panels/<PPPP>/
          panel.png               # cropped panel (from source)
          color.png               # colorized master (ratio-independent)
          kf_169.png  kf_916.png  # per-ratio keyframes (outpainted)
          clip_169.mp4 clip_916.mp4  (+ clip_1080p_*.mp4 upscaled)
          audio/                  # per-panel dialogue/moan/SFX clips + mix
        chapter_audio/            # mixed per-ratio audio bed
        subs/                     # .ass per language
        out/chapter_169.mp4 chapter_916.mp4   # DELIVERABLES
        chapter.json              # chapter-level state + phase
      state/queue.json, paused    # global scheduler state
```

Panel lifecycle: `DETECTED → ORDERED → GATED → SCRIPTED → COLORIZED →
KEYFRAMED(per-ratio) → QUEUED → GENERATING → GENERATED → DOWNLOADED →
VALIDATED → UPSCALED → ASSEMBLED` (+ `FAILED`, `NEEDS_REGEN`, and the two
awaiting-review states). Every state transition is written to disk before
any side effect — crash anywhere = re-run the step, it skips finished work
(idempotent, vidgen doctrine).

Job keys mirror vidgen's collision-proofing: ComfyUI output prefix
`<series>_<chapter>_<panel>_<ratio>_<ts>` and input upload names
`<panel>_<ratio>_kf_<rand>.png`.

## 7. Character registry — the living anchor

`registry/characters.json` + `assets/`:

```jsonc
{
  "id": "akira", "version": 3,
  "identity": {"hair": "black undercut", "eyes": "grey", ...},   // palette + descriptors
  "voice": {"engine": "gpt_sovits", "ref_clip": "assets/akira_voice_v2.wav",
            "fallback_engine": "indextts25"},
  "variants": [
    {"id": "school", "version": 2, "sheet": "assets/akira_school_v2.png",
     "first_seen": "ch01/p003"},
    {"id": "casual", "version": 1, "sheet": "assets/akira_casual_v1.png",
     "first_seen": "ch04/p018"}
  ]
}
```

- **Discovery:** script stage (A4) proposes new characters and new outfit
  variants from incoming pages; known character + unseen outfit → new
  variant proposal. Proposals land in the **card review gate** (A5) — you
  approve additions, never re-approve the existing cast.
- **Sheets:** generated by colorizing the character's cleanest canonical
  panel (vision-picked) with MangaNinja + the LLM's palette proposal
  (cross-checked against any color pages/covers in the source). One sheet
  per variant. Human approves once.
- **Voice refs:** per character, a ~5s JP clip (user-dropped, or picked
  from a small bundled original-voice library — see open questions). Card
  review approves it with the sheet.
- **Versioning:** edits create vN+1; every panel records
  `{card, variant, version}` used for colorize/keyframe/render. A registry
  fix NEVER rewrites finished output silently — affected panels are listed
  as NEEDS_REGEN; `koma regen` re-renders exactly those (via ref2va lane).
- Color-source series: colorization stage skips; panels themselves seed
  the sheets (best canonical panel per variant).

## 8. Audio design (locked: layered stack)

Per panel, built during script + GPU stage:

1. **Dialogue lines** (JP, verbatim from OCR, lightly normalized for TTS)
   — speaker-tagged from the character registry. Synthesized on the box by
   the character's TTS engine/voice-ref. Emotion cues from the script stage
   (GPT-SoVITS: emotion via reference-audio choice; IndexTTS-2.5 lane:
   text-described delivery for the heavy scenes).
2. **Moans/gasps/SFX** — event-timed (script stage marks events; hentai
   panels are often *more* moan than dialogue). Sourced from the licensed
   packs, pitch/tempo-adjusted per character where needed.
3. **Ambience** (room tone, rain, crowd) — library/MMAudio, per-scene.
4. **BGM** — per-mood pool, resting gain **0.25**, sidechain duck under
   speech (vidgen's proven filtergraph, `asplit=2` pattern, no corrective
   post-duck boost — decision #31 inherited).

Panel duration = its full audio bed (dialogue + events + tails), then
frame-grid-snapped for the clip — audio-first sizing, inherited. Chapter
mix = per-panel blocks audio-locked to clips (pad audio tail with silence,
never touch video).

Subtitles: ASS, **EN primary + zh-HANS second track** (config list,
`subtitle_languages = ["en", "zh-HANS"]`, more later). Burn primary via
Pillow fallback (brew ffmpeg lacks libass — inherited gotcha), soft-mux
the rest.

## 9. GPU etiquette & scheduler (locked: manual switch only)

- `koma pause` — drain semantics: the in-flight job finishes (~a minute
  for i2v), then the scheduler submits nothing. `--now` hard-stops
  (ComfyUI `/interrupt` + in-flight job reverts to QUEUED).
- `koma resume` — clears the flag; queue continues.
- **Machine-off ≡ paused:** SSH probe fails → hold, retry on monitor tick.
  No auto-boot while paused (inherited idle-gate: boot only when work is
  queued AND not paused).
- One ComfyUI job in flight at a time (sequential queue — the box is also
  in desktop use; VRAM contention caused real CUDA faults in vidgen runs).
- TTS batches scheduled while the video queue is drained/paused (or CPU
  mode for tiny jobs) — avoids 21+4GiB co-residency during denoise.
- Scheduler lives on the **Mac** (`koma monitor` daemon / cron `--once`),
  state in `data/…/state/`. It survives Mac reboots and box reboots:
  lost-in-crash jobs (in `/queue` neither history nor pending) → FAILED →
  re-queueable, inherited from vidgen decision #32.
- `koma status` shows: queue depth, in-flight job + ETA, per-chapter
  progress, pause state, box online/offline.

## 10. Repo layout & CLI

```
~/dev/manga-animantor/
  PLAN.md  DECISIONS.md  README.md
  docs/research/           # dossiers + corpus (from this planning round)
  src/koma/                # Python 3.11+ package (pydantic v2, typer, httpx, jinja2)
    models/                #   domain: Series, Chapter, Page, Panel, CharacterCard, Job
    storage/               #   series store (JSON-on-disk protocol, vidgen pattern)
    clients/               #   OllamaClient (text+vision), OpenAICompatClient, TTSClient, SSHRunner
    gpu/                   #   ComfyUIWorker (submit/poll/download/free_vram), sizing (frame grid),
                           #   boot state machine (wmic detach — inherited, exact recipe in skill)
    steps/                 #   A1..C4 as idempotent step functions, runner + registry
    scheduler/             #   queue, pause switch, monitor loop
    adapters/              #   model-adapter registry (§11)
    review/                #   dark-theme HTML contact sheets + card sheets (generator + tiny local server)
    audio.py  video.py  encode.py   # one encoder profile (vidgen #27 pattern)
    cli.py
  config/
    config.toml            # paths relative to PACKAGE ROOT (vidgen TOML trap — inherited)
    models/*.toml          # adapter specs (§11)
    workflows/*.json       # ComfyUI workflow templates (injection maps in spec)
    sfx/  bgm/  voices/    # pools + bundled voice-ref library
    fonts/
  data/                    # gitignored (§6)
  tests/                   # pytest; real-binary regression tests for every shell-out
  Makefile
```

**CLI name: `koma`** (コマ = manga panel — the atomic unit). Global install
identical to vidgen's: launcher `~/.local/bin/koma` → persistent venv
(`~/.local/share/koma-venv`, uv-managed 3.11) with editable install —
source edits take effect with no reinstall.

Command surface (v1):

```
koma new <slug> [--direction rtl|ltr|auto] [--lang ja|ko|...]     # create series
koma ingest <slug> <path> [--chapter N]      # import JPEG/PDF → panels → order review queue
                                             # (PDF: rasterize via mutool/pdftoppm; webtoon = long-strip mode)
koma review <slug>                          # serve dark-theme contact sheets (order + cards)
koma gate <slug>                            # run age-gate (or auto-run inside ingest — fail-closed)
koma script <slug> [--chapter N]            # A4+A5 (LLM/vision, cheap)
koma render <slug> [--chapter N] [--ratio 16:9,9:16] [--stop-after queue]
koma monitor [--once]                       # scheduler tick: submit/poll/download/advance/assemble
koma pause [--now] / koma resume            # GPU etiquette switch (§9)
koma status <slug> / koma health            # progress / env check
koma assemble <slug> --chapter N [--ratio …]  # C1–C3 (idempotent, re-runnable)
koma merge <slug> --chapters 1-6 --ratio 16:9  # no-reencode binge file
koma regen <slug> --panel <id> [--ratio …]  # ref2va lane re-render (§5.3)
koma models list                            # every role × profile: installed? hash-verified? probed? default? pinned where?
koma models install <profile>               # download → sha256 verify → doctor → live probe → selectable (§11.1)
koma models default <profile>              # set a role's default (video lane, vision, TTS, text, …)
koma models pin <slug> <profile>            # freeze a series onto a profile (painter consistency, §11.1)
koma models ab <slug> --panel <id> --profiles a,b  # same panel+seed → N clips → dark-theme side-by-side sheet
koma setup <model-id>                       # low-level one-time install (what `models install` drives)
koma doctor                                 # verifies workflows' node IDs LIVE via /object_info,
                                             # frame grids, service reachability, TTS server, packs
```

Defaults: `koma render` without `--ratio` renders the series' configured
ratios (both if configured). All long work goes through the queue — no
command blocks the terminal for GPU durations.

## 11. Model adapters — specs, not code (locked)

Every swappable engine is data in `config/models/<id>.toml`:

```toml
id = "minimax_h3_fl2va_turbo"
kind = "video_i2v"                  # video_i2v | video_ref | colorize | outpaint | tts |
                                    # vision | text | ocr | agegate | upscale
provider = "comfyui"
workflow = "workflows/minimax_h3_i2v_turbo_v2.json"
[inject]                            # verified node IDs (koma doctor re-verifies live)
keyframe = "6.inputs.image"
motion   = "7.inputs.prompt"
length   = "7.inputs.length"
fps      = "14.inputs.fps"
output   = "15.inputs.filename_prefix"
frame_grid = "auto"                 # live /object_info (fallback table hardcode — vidgen #38)
[setup]                             # koma setup consumes this
weights = [ {url=…, dir="models/diffusion_models"}, … ]
custom_nodes = []                   # H3 = ComfyUI core, nothing to install
```

- **One-time `koma setup <id>`**: downloads weights into the box's
  `ComfyUI-Shared/models` tree (via the existing `extra_model_paths.yaml`
  wiring — inherited; if model dirs ever look empty, re-copy that yaml
  before assuming weights are missing), clones/pins custom node packs,
  then `koma doctor` verifies node IDs + frame grid live before first use.
  ref2va, MangaNinja, Qwen-Image-Edit, GPT-SoVITS, IndexTTS-2.5 each get a
  spec + setup.
- Swapping a model = add a spec, run setup, change one line in
  `config.toml` (`[video] lane_default = …`). Adding PinkCherry or the
  Heretic encoder swap later = same mechanism, zero code.
- TTS engines are HTTP-service adapters (GPT-SoVITS ships an API server;
  IndexTTS-2.5 gets a thin wrapper service) — `koma setup` installs and
  registers them on the box; the scheduler starts/stops them per §9.

### 11.1 Video profiles = composable components (decision #020)

A video "model choice" is a STACK, not one file: **reader** (text encoder)
+ **painter** (DiT/UNET) + LoRA + VAEs, over a workflow. Specs come in two
layers:

- `config/models/components/<id>.toml` — one weights artifact: file, sha256,
  target dir (ComfyUI-Shared tree), format (`unet` | `encoder` | `lora`),
  provenance notes.
- `config/models/profiles/<id>.toml` — a lane definition: workflow JSON +
  inject map (**including `unet` and `clip` injection points**, so weight
  swaps are data, not code) + frame grid + component references.

Worked examples (the two real community H3 mods — dossier 1):

- `pinkcherry_fl2va` — SexGod1979 PinkCherry, **pruned-int8 20.97GB flavor**
  (the 34GB int8_convrot flavor does NOT fit 24GB; the pruned flavor matches
  our workflow's existing loader format). DiT finetune on adult content;
  drop-in for the fl2va UNETLoader slot. Alphas v0.1–v0.5 + own
  "NaughtyTimes" LoRA. **fl2va-only — no ref2va equivalent exists.**
- `heretic_encoder` — ethanfel Qwen3-VL-32B-Ultra-Heretic (NVFP4/AWQ via
  Momoking), abliterated text encoder, drop-in for the CLIPLoader
  (type=minimax). Likely valid for BOTH lanes (shared encoder slot) —
  verify by probe.

Profiles: `h3_fl2va_turbo` (stock) · `h3_pinkcherry_turbo` (painter swap) ·
`h3_pinkcherry_heretic` (both swaps). Rules:

- `koma setup` **hash-verifies every component** — community H3 artifacts
  have a scam/malware history (the fake "MINIMAX-H3-NSFW" reupload).
- After setup: `koma doctor` verifies nodes/grid LIVE, then a one-clip live
  probe renders before the profile becomes selectable. Turbo-LoRA ×
  PinkCherry compatibility is unverified — the probe decides, per profile.
- **Neither is required for v1**: local stock H3 is already unfiltered.
  These are competence upgrades for explicit subject matter, adopted only
  if the A/B (same panel, same seed, vision-judged) wins.
- **Profile is pinned per series** (`series.json: video_profile`) — never
  mix painters mid-series (a DiT swap visibly changes the look). Per-chapter
  override exists for A/B testing only.
- ref2va regen lane stays on stock weights until a ref2va finetune exists.

### 11.2 Choosing models — the multi-choice UX (decision #021)

One umbrella command covers every swappable role (video profiles, vision,
TTS, text LLM, colorizer, outpaint, age-gate classifiers):

```
koma models list                    # all roles × profiles: installed / hash-verified /
                                   #   probed / default / pinned-to-which-series
koma models install <profile>       # download → sha256 verify → doctor (nodes+grid live)
                                   #   → one-clip probe → registered & selectable
koma models default <profile>       # role default (writes the config.toml line)
koma models pin <slug> <profile>    # per-series freeze (video profiles)
koma models ab <slug> --panel <id> --profiles a,b
                                   # A/B: same panel, same seed, N profiles → N clips
                                   #   → dark-theme side-by-side contact sheet for judgment
```

UX principles:

1. **Zero decisions to run v1** — stock profiles are pre-registered defaults;
   you never touch `koma models` unless you want to.
2. **One list, one install command, one switch command** — a new model
   never means code, config surgery, or workflow-file editing.
3. **Evidence before adoption** — `koma models ab` renders the same panel
   under each candidate profile and presents a side-by-side sheet; adopting
   PinkCherry/Heretic (or any future model) is a judgment call on real
   output, not hype.
4. **Pins protect consistency** — a series frozen to a profile can't be
   silently switched mid-story; changing a pin is explicit and logs what
   would need re-rendering.
5. **Doctor catches drift** — ComfyUI version bumps that shift node IDs
   or grids surface at check time, not render time.

Concrete recipe (the PinkCherry painter swap — Heretic is identical with
`format = "encoder"` injecting `3.inputs.clip_name`):

```toml
# config/models/components/pinkcherry_fl2va.toml
id = "pinkcherry_fl2va"
format = "unet"                        # swaps the UNETLoader slot
file = "pinkcherry_minimax_h3_fl2va_pruned_int8.safetensors"
sha256 = "<pin at first install, verify on every use>"
source = "https://huggingface.co/SexGod1979/PinkCherry_MiniMax-H3"
target_dir = "diffusion_models"        # ComfyUI-Shared models tree
provenance = "community finetune of MiniMax H3 fl2va on adult content; \
              license-restricted base — local private use, never redistribute"

# config/models/profiles/h3_pinkcherry_turbo.toml
id = "h3_pinkcherry_turbo"
kind = "video_i2v"
workflow = "workflows/minimax_h3_i2v_turbo_v2.json"   # SAME workflow as stock
components = { unet = "pinkcherry_fl2va", clip = "stock_h3_encoder" }
[inject]
unet = "1.inputs.unet_name"            # doctor re-verifies node IDs live
keyframe = "6.inputs.image"
motion   = "7.inputs.prompt"
length   = "7.inputs.length"
fps      = "14.inputs.fps"
output   = "15.inputs.filename_prefix"
frame_grid = "auto"                    # live /object_info, 17n+5 expected
```

## 12. Dual-ratio strategy (locked: 2 full generations)

When a series wants both 16:9 and 9:16:

- **Shared, paid once:** everything in Stage A, character sheets, and
  **colorization masters** (ratio-independent).
- **Per-ratio, full quality:** keyframe reframe (outpaint to target ratio)
  → full i2v render → upscale. No crop-the-rendered-video shortcuts, no
  vision-check-per-crop (explicitly rejected — quality-first, and the
  per-panel vision checks happen anyway at the existing validate step).
- Cost ≈ 50–70% of the first ratio's GPU time for the second (audio,
  translation, colorization already paid).
- Chapters can render one ratio now, the other later — they're independent
  queues keyed `(panel, ratio)`.

## 13. Build phases

Each phase ends runnable + tested; phases 1–2 need zero GPU.

- **Phase 0 — Scaffold.** Repo, package skeleton, domain models, series
  store, config layering (env secrets, never in config.toml), CLI skeleton,
  `koma health`, Makefile, pytest harness. Deliverable: `koma new/status`
  works.
- **Phase 1 — Ingest & order.** JPEG/PDF import (rasterize PDFs),
  panel detection (adopt Comic Panel Extractor as library or CLI —
  evaluation spike first, panelforge as alternative), direction heuristic
  (script distribution via manga-ocr: JP-vertical → RTL, hangul → LTR;
  gutter analysis fallback; webtoon long-strip mode), panel crops,
  `pages.json`, dark-theme order-review contact sheets (click-to-swap,
  incremental per batch). Deliverable: a real chapter ingested + certified.
- **Phase 2 — Gate & script (Mac).** Age-gate (two classifiers, OR-reject,
  fail-closed, red-team tests with seeded minor-art examples — gate MUST
  block), manga-ocr extraction, local vision captions (Qwen3-VL-8B-abliterated
  via Ollama on Mac), speaker attribution, dialogue normalization,
  translations (EN, zh-HANS via glm-5.3-flash), motion plans per panel per
  ratio, timing (audio bed estimate), character/variant discovery → card
  review gate. Deliverable: `script.json` + approved registry for a real
  chapter, zero GPU used.
- **Phase 3 — Colorize (GPU lane opens).** Model-adapter registry + `koma
  models` (list/install/default) proven on the first real install
  (`koma setup manganimu`), palette-proposal flow, canonical-panel →
  character sheets, per-panel colorization with variant-pinned refs,
  `free_vram` between stages, monitor + pause/resume wired (§9).
  Deliverable: a colorized chapter, pause-drain-resume proven on the live box.
- **Phase 4 — Keyframes & queue.** `koma setup qwen_image_edit`, outpaint
  per ratio, motion prompts → job payloads, `koma render --stop-after
  queue`. Deliverable: keyframes for both ratios on disk.
- **Phase 5 — Render & monitor.** fl2va turbo lane (reuse vidgen's workflow
  JSON + injection map as spec v1), audio-first sizing on 17n+5 grid,
  submit/poll/download/reconcile, vision validation (content + identity),
  Real-CUGAN upscale (per-ratio toggle), `koma models ab` + `koma models
  pin` for video profiles. Deliverable: validated, upscaled clips for a
  full chapter.
- **Phase 6 — Assemble & deliver.** Audio mix (§8), ASS subs (EN+zh-HANS,
  Pillow burn), per-panel audio-lock concat, chapter videos per ratio,
  `koma merge`, `post_metadata.json`-style provenance file. Deliverable:
  **v1 acceptance run** (below).
- **Later (2.x):** PinkCherry + Heretic adoption (install recipes are
  specced §11.1–11.2 — `koma models install` → `koma models ab` on a real
  panel → adopt + pin per series if the A/B wins), ref2va regen lane +
  panel-chain conditioning experiments (both specced now, built after v1
  proves out), IndexTTS-2.5 emotion lane, MMAudio ambience, more subtitle
  languages, voice library expansion, binge-file extras, webtoon vertical
  deliverable mode.

**v1 acceptance run (the definition of done):** one real B&W Japanese
hentai chapter (30–40 panels), adults-only confirmed by the gate, through
ingest → both review gates → both ratios rendered → two chapter videos
(JP voices, EN+zh-HANS subs, soft BGM), unattended except the two gates;
mid-render `koma pause` → box usable → `koma resume` → completes with zero
lost work; a seeded age-gate test page is rejected fail-closed.

## 14. Verification & testing strategy (inherited doctrine)

- pytest, mocked units for logic; **real-binary regression tests** for
  every ffmpeg/shell-out (the `-vsync` bug class — mocks can't catch
  unsupported flags; use `-fps_mode passthrough`, ffmpeg 7).
- Live GPU probe script after ANY workflow/node/model change (vidgen's
  `probe_gpu_worker.py` pattern): boot → submit one real clip → assert
  rendered frames == requested (grid) → ffprobe. Use REAL artifacts, not
  synthetic fixtures.
- `koma doctor` as a first-class command: node IDs verified against
  `/object_info` LIVE (never trust plan-doc node IDs — vidgen's own i2v
  node IDs shifted between revisions), frame grids, service health, pack
  presence.
- Age-gate red-team suite: seeded positive cases MUST block; model
  error/timeout MUST fail closed (job dies, never proceeds).
- Idempotency test per step: run twice → second run is a no-op.
- Crash drill: kill monitor mid-chapter → restart → work continues, no
  zombie jobs, no lost downloads.
- Cost/timing table maintained in `docs/timings.md` as phases land
  (measured, not estimated — vidgen's `production-timings.md` convention).

Known-measured anchors so far (from vidgen on this box): warm boot ~15s,
cold boot ~3min (timeout 240s), i2v clip ~70s warm @480p turbo, realcugan
upscale minutes-per-clip (VRAM-contended ~11min once — hence free_vram
first), 142-frame extraction/burn minutes. Chapter-level totals are
estimates until Phase 5 measures them; present as such.

## 15. Open questions (not blocking Phase 0–2)

1. **Voice-ref sourcing** — bundled small library of original synthesized
   voices vs. user-dropped refs per character. Plan: support both; seed a
   tiny library in Phase 2; Pooh curates per series. (Needs his ears —
   quality bar is his.)
2. **Review app mechanics** — static HTML + JS writing JSON via a tiny
   local server (`koma review` serves it). Fine to keep minimal; dark theme
   (standing preference).
3. **BGM pool sourcing** — free/libre packs vs. a small paid pack;
   per-mood (romantic/tense/playful) curation in Phase 6.
4. **Subtitle render mode** — burn EN only + soft-mux zh-HANS vs. burn
   both stacked. Default proposal: burn EN, soft-mux the rest (players
   pick up soft tracks); revisit after first watch-through.
5. **Upscale default per ratio** — on for both vs. 16:9 only. Real-CUGAN
   time is real; decide from Phase 5 measurements.
6. **Panel detection engine pick** — Comic Panel Extractor vs. panelforge
   (Phase 1 spike on real pages decides; contact-sheet review covers
   residual errors either way).

## 16. Explicitly deferred / rejected

- **Implementation of anything above** — this round is plan-only.
- **Crop-shortcuts for the second ratio** — rejected (quality-first; two
  full generations).
- **Auto-yield GPU heuristics** — rejected (manual switch only; §9).
- **ElevenLabs for adult dialogue** — rejected (policy/filter risk).
- **Feeding previous clips as default ref video** — deferred to the 2.x
  regen toolbox (§5).
- **Ever-growing single series video** — rejected; chapter files + merge.
- **"NSFW" reupload model repos** — never install (scam/malware vector).