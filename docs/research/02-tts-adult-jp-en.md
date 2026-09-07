# Dossier 2 — TTS for adult (fictional, drawn-manga) dialogue

Researched 2026-09-08 against primary sources (policies, READMEs, HF model
cards/licenses).

## ElevenLabs — NOT suitable for adult dialogue

Prohibited Use Policy (pulled live, updated 17 Aug 2026):
- §1b bans sexual material **targeting minors** (not all sexual material).
- §2c bans facilitating "sexual services" (transactional, not erotica).
- §5b bans cloning a REAL person's voice for sexualization — fictional/
  original voices not addressed.
- §8 fiction carve-out: policy "does not apply to activity in purely
  fictional contexts."
- §9a: free tier may not be used commercially at all; §9c: **Sound Effects
  outputs cannot be commercially used standalone**.

**Enforcement reality:** automated pre-generation text filtering
flags/censors sexual language and profanity at TTS time (r/ElevenLabs
threads document identical text passing one day, blocked the next);
anecdotal bans for sustained NSFW use. Written policy tolerates fictional
adult content but the filter makes explicit output unreliable and repeated
flags risk suspension. Eleven v3 audio tags ([whisper], [sigh]) are the
quality ceiling — usable only if text passes the filter. **Verdict: keep
for clean narration elsewhere; do not build this pipeline on it.**

## Local self-hosted candidates (3090 shared with ComfyUI)

| Model | License | Cloning | VRAM | JP quality | Emotive control |
|---|---|---|---|---|---|
| **Style-Bert-VITS2** | repo AGPL-3.0; bundled default voices (amitaro/koharune) **ban R-18**; JVNV models CC BY-SA 4.0 | training-based (many 2–14s clips + transcripts) | **runs on CPU** | best-in-class anime/VTuber JP | style vectors (whisper/angry…) continuous strength |
| **GPT-SoVITS** | **MIT** (cleanest) | **5s zero-shot; 1min fine-tune** | ~2–4 GB (RTF 0.028 @4060Ti) | good, cross-lingual EN/JA/KO/ZH/YUE | via reference-audio emotion transfer |
| F5-TTS | **CC-BY-NC-4.0** (non-commercial — disqualifying) | ~10s zero-shot | 2–4 GB | weak (ZH/EN only) | limited |
| CosyVoice 2 / Fun-3 | Apache-2.0 | seconds, zero-shot | 2–4 GB (0.5B) | solid (9 langs incl. JA) | instruct-based |
| **IndexTTS-2 / 2.5** | bilibili Model Use License (free <100M MAU / RMB 1B revenue) | zero-shot, single wav | 8 GB min / 16 GB rec | 2.5 (Aug 2026) adds **Japanese + kana control** | **best: timbre–emotion disentanglement + text-described emotion** |
| Kokoro-82M | Apache-2.0 | none (fixed bank) | <1 GB, CPU-feasible | decent (misaki[ja], single JP voice) | minimal |
| **Qwen3-TTS** (Jan 2026) | Apache-2.0 (Base 0.6B/1.7B) | **3s cloning** | 0.6B ≈2–3 GB; 1.7B ≈5–6 GB | strong (5M hrs) | instruct-based + adaptive tone |
| Chatterbox Multilingual V3 | MIT (0.5B, 23 langs) | zero-shot, seconds | ~2–4 GB | newer/less JP-proven | **emotion exaggeration slider** |

None restrict adult content in-license (SBV2's R-18 ban = bundled voices
only — train/merge your own or use JVNV; bilibili restricts scale, not
content; local inference has no text filter).

**Co-residency with ComfyUI (video DiT ~21 GB):** realistic while video
renders = Kokoro, GPT-SoVITS, CosyVoice, Qwen3-0.6B, Chatterbox (2–4 GB);
SBV2 in CPU mode; IndexTTS-2.5 (8–16 GB) needs the GPU alone → batch
dialogue between video runs. Practical pattern: **TTS sessions while the
video queue is drained/paused.**

**Recommendation (adopted as decision #003):** GPT-SoVITS primary (MIT,
5s/1min cloning), IndexTTS-2.5 optional emotion lane; Qwen3-TTS as the
notable alternate; SBV2 custom-trained voice if maximum JP quality and a
dataset are available.

## Cheap NSFW-friendly commercial alternatives

Only found: **HyperVoice** (taskagi.net/free-nsfw-text-to-speech) — "no
word filters", ~$10 packs, Seductive Whisper/Dominant/Intimate voices;
small vendor, quality unverified — experiment, not pipeline. No major
vendor (PlayHT, Cartesia, Murf, Fish) advertises NSFW-permitting terms.
**Local open-weight models are the only dependable uncensored route at
ElevenLabs-tier quality — and the cheapest at scale.**

## Adult SFX/ambience

- **Libraries (adopted):** itch.io "Female Moan Sound Effects Pack" (~$10,
  20 min, magicalmysticva); free CC packs on Freesound (magicalmysticva
  packs); Soundsnap "sex" tag (500k+ SFX, subscription). Room tone:
  Freesound + standard libraries.
- **Generation:** MMAudio (MIT, video-to-audio, hkchengrex/MMAudio) for
  ambience synced to rendered video; Stable Audio Open 1.0 (Stability
  community license — check revenue cap).
- **ElevenLabs SFX is unusable commercially (§9c).**