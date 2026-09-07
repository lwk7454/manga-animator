# Dossier 3 — LLM choices (uncensored text + vision, cheap) & age-gate

Researched 2026-09-08 against primary sources (ollama.com, openrouter.ai
live API + terms, HF model cards, WD tag CSV, Civitai classifier repo).

## Ollama Cloud

Pricing ($/M in/out, Sep 2026): gpt-oss:20b 0.07/0.30 · nemotron-3-nano
0.06/0.24 · gemma4 (vision) 0.14/0.40 · **glm-5.3-flash (vision, 18B
active) 0.15/0.50** · gpt-oss:120b 0.15/0.60 · deepseek-v4-flash 0.22/0.66
· minimax-m2.7 0.30/1.20 · mistral-large-3 (vision) 0.50/1.50 ·
deepseek-v4-pro 0.66/1.98 · minimax-m3 (vision) 0.60/2.40 · qwen3.5:397b
0.60/3.60 · glm-5.1/5.2/5.3 1.00–1.40/3.20–4.40 · kimi-k3 3.00/15.00.
Peak pricing = 2× input on some models 12:00–18:00 UTC weekdays. Cloud
vision: glm-5.3-flash, gemma4, qwen3.5, minimax-m3, mistral-large-3,
kimi-k2.6/k3.

**Policy:** ollama.com/terms §4 is generic ("harmful, offensive, or
illegal") — **no adult-fiction-specific clause anywhere on the site**.
GitHub issue ollama/ollama#14153 (Feb 2026) is a bug report that a model
generated explicit sexual content from the word "exit" — i.e., no
server-side block. No dolphin variant on Cloud (dolphin-mixtral 8x7b 26GB
/ 8x22b 80GB, dolphin3 8b 4.9GB — local-only, text-only). Refusal behavior
per-model is NOT primary-documented — A/B test yourself. Best cloud bets:
glm-5.3-flash (cheapest vision, JP-strong), mistral-large-3. Worst: gemma4,
gpt-oss (safety-tuned).

## OpenRouter (428 models live)

**Policy:** OpenRouter doesn't filter content itself — routes to providers
that enforce their own. Hard limits (terms §6.7/6.9): automated CSAM
screening on ALL inputs, NCMEC reporting (18 U.S.C. §2258A). **Legal adult
fiction is permitted.** Moderation-flagged responses can trigger fallbacks;
pin/exclude providers via the `provider` routing object.

Free: gemma-4-31b-it (vision), gemma-4-26b-a4b, nemotron-3-nano-omni
(image), thinkingmachines/inkling (image, 1M ctx), nemotron-3.5-content-
safety (image+text **safety classifier — free age-gate helper**),
openrouter/free router. Rate limits ~20 req/min, 200/day free.

Uncensored text: dolphin-mistral-24b-venice 0.20/0.90 (explicitly
uncensored, text-only); cydonia-24b 0.30/0.50; NousResearch Hermes 4 70B
0.13/0.40. **No uncensored/abliterated VISION model is hosted on OpenRouter**
— uncensored options are all text-only. Cheap stock vision: qwen3-vl-32b
0.104/0.416, qwen3-vl-8b 0.117/0.455, mistral-small-3.2 0.075/0.20, glm-5.3-
flash 0.075/0.25, glm-4.6v 0.30/0.90, gemma-3-27b 0.08/0.45, nova-lite 0.06/
0.24. **These refuse on explicit hentai frames to varying degrees.**
**Conclusion: for LOOKING at explicit images, go local.**

## Local on the 3090 / Mac

- **`huihui-ai/Huihui-Qwen3-VL-8B-Instruct-abliterated`** — top pick.
  Apache-2.0, "will no longer say 'I can't describe or analyze this image'".
  Ollama-ready `huihui_ai/qwen3-vl-abliterated:8b` = 6.1GB (Q4), 256K ctx.
  Qwen3-VL is JP-strong. 32B abliterated GGUFs exist (≈19–20GB Q4) — fits
  24GB only without ComfyUI loaded.
- `huihui-ai/Qwen2.5-VL-7B-Instruct-abliterated` — same recipe (6.0GB).
- `shutkit/Qwen2.5-VL-7B-NSFW-Caption-V3-abliterated` — purpose-built NSFW
  captioner (danbooru-style tagging of explicit frames).
- `mlabonne/gemma-3-27b-it-abliterated` — vision-capable but "much more
  resilient to abliteration", experimental.
- Text-only uncensored local: dolphin-mixtral 8x7b = 26GB (doesn't fit 24GB
  without CPU offload, 2 years old, weak JP) — prefer dolphin3:8b (4.9GB)
  or Qwen abliterated 8–14B.
- **`kha-white/manga-ocr-base`** (Apache-2.0, 889K downloads) — manga OCR:
  vertical text, furigana, overlaid text, low-quality scans. Pair with
  Qwen3-VL for scene understanding.

## Age-gate (adopted as decision #013)

- **`Civitai/Age-Classification-SigLIP2-anime-finetune`** — SigLIP2
  fine-tuned for anime age (adult/minor). Shipped calibration:
  child_threshold=0.622, **child precision 0.995, recall 0.936, F2 0.947**;
  Civitai test anime subset: acc 0.859, P 0.821, R 0.837. ~16% of anime
  minors missed at default threshold → **tune threshold down** (bias to
  recall).
- **WD Tagger v3** (`SmilingWolf/wd-swinv2-tagger-v3`, Apache-2.0) —
  Danbooru-trained, 10,861 tags; tag list literally contains `loli` (id
  128), `shota` (id 2614), `child` (id 12667) + rating categories —
  orthogonal minor-content signal for illustrated art.
- **`meta-llama/Llama-Guard-4-12B`** — 12B multimodal safety classifier
  (MLCommons taxonomy incl. child-sexual-exploitation S1); on OpenRouter
  $0.18/$0.18 with image input. Free alternative: nemotron-3.5-content-
  safety:free. (HF repo gated — verified via API/metadata.)
- Weak/avoid: deepghs/anime_rating (74% acc — rough); Falconsai/
  nsfw_image_detection (photoreal ViT — weak on anime).

**Fail-safe architecture (adopted):** gate BEFORE any generation; **OR-logic
rejection** (any of WD loli/shota/child ≥ threshold, Civitai minor-prob ≥
tuned-down threshold, Llama-Guard-4 S1 flag → reject); **fail closed** on
any model error/timeout/low-confidence; per-page AND per-chapter; log every
rejection for audit; thresholds biased to recall. Gate models are tiny
(SigLIP2 ~0.4B, WD ~400M, LG4-12B ~7GB quantized / or $0.18 via OR) and run
on the Mac alongside everything else.

## Recommended minimal-cost stack (adopted as decision #015)

- Vision/captioning (local): Qwen3-VL-8B-abliterated (+ shutkit NSFW-Caption
  as optional caption specialist) + manga-ocr.
- Age-gate (local, mandatory): Civitai SigLIP2 + WD v3 (+ Llama-Guard-4
  local or $0.18 OR), OR-reject, fail-closed.
- Text (translation/scripting): Ollama Cloud glm-5.3-flash ($0.15/$0.50,
  JP-strong); uncensored fallbacks: dolphin-mistral-24b-venice ($0.20/$0.90)
  or local Qwen-abliterated.