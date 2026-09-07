# Dossier 1 — MiniMax H3 (Turbo v2) in ComfyUI

Researched 2026-09-08 against primary sources (ComfyUI core source, official
node docs, model cards, 3090 measurements). Raw corpus: `corpus_minimax_h3/`.

## Nodes (ComfyUI core since v0.30.0 — `comfy_extras/nodes_minimax_h3.py`)

- **`MiniMaxH3ImageToVideo`** (fl2va): `prompt`, `width/height` (default
  1344×768), `length` (**17k+5** frame grid, default 124 ≈ 5s), optional
  **`first_frame` AND `last_frame`**. No reference-image input.
- **`MiniMaxH3ReferenceToVideo`** (ref2va): up to **9 `ref_images` + 3
  `ref_videos` (each may carry paired soundtrack) + 3 standalone
  `ref_audios`** — 12 files total, ≤15s combined video, ≤15s audio.
  Prompt addresses them as `<Picture i>` / `<Video k>` / `<Audio j>`.
  `ref_image_size`: `"match"` (fast) vs `"max"` (2048px short edge, best
  identity, **several times slower** — ref tokens ride every sampling step).
  Uses DIFFERENT weights (`minimax_h3_ref2va_*`) than fl2va.
- **`MiniMaxH3AddGuide`**: anchors a still image / short clip (5/22/39…
  frames) / audio at ANY frame index; chainable for multi-shot; enables
  video extension ("feed first 22 frames + audio of an existing video →
  model generates the continuation").
- **`MiniMaxH3FunControlNetApply`**: closest to true v2v — Fun ControlNet
  Union patch (`control_video`, `mask`, `source_video`: structure/motion
  control + inpainting).
- Also: `EmptyMiniMaxH3LatentAV`, `MiniMaxH3SigmaShift` (shift_video 12 /
  shift_audio 3); per-token latent noise masks (ComfyUI PR #15375) for
  local inpainting/extension via `denoise_mask`.

**Image ref + video ref simultaneously: YES** — one ReferenceToVideo
generation mixes all ref types. Prompt assigns roles ("Image 1 is the hero,
move like Video 1"). Two ways to get a start frame PLUS refs:
(1) official: R2V + AddGuide pinning the panel at frame 0 ("Picture 1 is
both the identity lock and the first frame" — docs.comfy.org tutorial);
(2) third-party combined node (entropicnoise/MiniMax-H3-Combined-Image-And-
Reference-to-Video, needs ComfyUI ≥0.33.1).

## VRAM — 24GB RTX 3090 (smeltcore's 3090 Ti recipe, byte-exact from Comfy-Org tree)

| Stage | Weights | + reserve | Total | Fits 24GB? |
|---|---|---|---|---|
| Text encode (nvfp4 Qwen3-VL-32B encoder) | 14.61 GiB | 1.19 | 15.8 | yes (alone) |
| Denoise (pruned int8 DiT) | 19.53 GiB | 1.19 | 20.72 (Linux) / 21.01 (Windows) | yes, ~3 GiB spare |
| Decode (video VAE fp16) | 4.85 GiB | 1.46 | 6.3 | yes |

Encoder + DiT never co-resident (34.14 GiB > 24) — ComfyUI dynamic offload
evicts between stages automatically (costs swap time). Minimum spec 12GB
(3060-class, streaming); 24GB keeps denoise resident. Plain i2v, R2V
(refs), and v2v-style ALL fit: ref2va and fl2va transformers are the same
size (pruned int8 = 20.97 GB). Reference count/size raises activation
memory on top of the ~3 GiB headroom — keep refs at `"match"`, count low.

- **Canvas: local H3-Base is 768p only** (1344×768 cap; 2K needs unopen-
  sourced H3-Regenerate-2K). Trained range **124–362 frames (~5–15s @24fps)**.
- **Speed datapoints:** 9.00 s/it stock → 6.5 s/it with SageAttention
  (~1.37×) at 864×480/5s/20 steps (~180s → ~130s). Turbo LoRA cuts 20 → 4–8
  steps (~5× sampling speedup). Official lightx2v turbo LoRAs: fl2v 4/8-step
  v1.0 768p, **ref2v 4-step v0.1**. Ampere (sm_86) needs cu130 PyTorch for
  int8_convrot fast kernels; no native FP8.

## "Jailbreak"/uncensored builds

**The built-in filter is NOT in the weights** — moderation lives in
H3-Context-IR (hosted, not open-sourced): "user-submitted text, images and
videos … subject to automated moderation; content suspected of being
unlawful, pornographic … may be blocked" (model card, Safety Guardrails).
Local ComfyUI runs H3-Base directly; no hosted moderation is included in
the checkpoint (Kingy AI audit). **Local = unfiltered; nothing to bypass.**
License clause II.5 prohibits using it to "circumvent or bypass any safety
guardrails"; geofenced OUT of US/EU/UK/South Korea; no distillation.

What exists on HF:
- `Blackfrost-Research/MINIMAX-H3-NSFW` — **fake**: straight reupload of
  base repo, matching hashes, README-only change; malware vector; delisted.
- `SexGod1979/PinkCherry_MiniMax-H3` — **real** NSFW finetune (fl2va
  checkpoints: int8_convrot 34.04 GB / pruned-int8 20.97 GB, alphas
  v0.1–0.5) + "NaughtyTimes v3" fp32 LoRA. Same architecture/quant,
  drop-in for the fl2va loader.
- `ethanfel/Qwen3-VL-32B-Ultra-Heretic-H3-ComfyUI` — abliterated encoder
  swap (attacks prompt-understanding, not the DiT); NVFP4/AWQ quant by
  Momoking.
- NSFW LoRA collections (awesome-minimax-H3) — style/content LoRAs, not
  filter bypasses.