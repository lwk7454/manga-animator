# manga-animantor

Manga series → coherent chapter-by-chapter animated video, on local hardware.
`koma` (コマ — manga panel) is the CLI.

**Status: PLAN.** Nothing is implemented yet — this round is design only.

- **PLAN.md** — the full design & build plan (pipeline, model stack, state
  model, phases, acceptance run). Start here.
- **DECISIONS.md** — the authoritative decision log. Never re-litigate.
- **docs/research/** — primary-source model research (MiniMax H3 in
  ComfyUI, TTS for adult JP/EN, uncensored LLM/vision options), Sep 2026.

Headline properties:

- Anime-fication: each colorized panel is the keyframe; LLM-written motion
  prompts drive MiniMax H3 i2v on the local 3090.
- Adults-only content supported end-to-end (local models only where cloud
  models refuse); mandatory fail-closed age-gate before any generation.
- Character cards anchor identity (colors + JP voice) across indefinitely
  long series; outfit variants discovered whenever they appear.
- JP voices + EN/zh-HANS subtitles; layered audio (dialogue, moans/SFX,
  soft BGM).
- Every model is a swappable profile — `koma models install/default/pin/ab`.
  PinkCherry (adult-trained painter) and Heretic (abliterated encoder) drop
  in as alternative MiniMax H3 profiles with zero code (PLAN §11).
- 16:9 and/or 9:16 — two full generations, shared upstream.
- Long-running work is resumable, queue-based, and manually pausable at any
  moment; the 3090 is treated as a shared machine.