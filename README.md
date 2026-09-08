# manga-animator

Manga pages → coherent animated video. Plan: `PLAN.md` · decisions:
`DECISIONS.md` · research: `docs/research/`.

## Status — implementation begun (Sep 2026)

- **Phase 0 ✅ scaffold** — package, domain models, series store (atomic JSON),
  config layering, `koma new/status/health/pause/resume`, global launcher
  (`~/.local/bin/koma`, editable venv `~/.local/share/koma-venv`), 33 tests.
- **Phase 1 ✅ ingest & order** — JPEG/PDF import (pdftoppm/mutool), panel
  detection (OpenCV heuristic adapted from Comic-Panel-Extractor, GPLv3,
  DPI-independent + splash fallback), reading-order proposal (overlap-based
  row clustering, RTL/LTR, low-confidence flags), panel crops
  (`chapters/NN/panels/<PPPP>/panel.png`), dark-theme review sheet
  (`koma review`) + headless `koma certify --swap`.
- **Phase 2 ✅ gate & script (Mac, zero GPU)** — fail-closed age-gate
  (Civitai SigLIP2 age + WD-swinv2 tagger, OR-reject, model error ⇒ reject,
  audit log per chapter; LIVE-verified with real weights on synthetic pages),
  `koma gate/script/cards` commands, clients (Ollama text+vision,
  OpenAI-compat fallback, manga-ocr), script stage (caption → OCR → motion →
  EN/zh-HANS translation → audio-first timing on the 17n+5 grid),
  SCRIPT_FAILED rework lane + recovery. 68 tests green (gate red-team:
  error/timeout/missing-source all fail closed).
- Phase 3 (colorize — first GPU lane) next.

## Dev

```
make venv install   # once
make test           # pytest
koma --help
```

Adults-only project (18+): age-gate lands in Phase 2 and is fail-closed —
no generation without a passed gate (decision #013).

- JP voices + EN/zh-HANS subtitles; layered audio (dialogue, moans/SFX, soft BGM).
- Every model is a swappable profile — `koma models install/default/pin/ab`.
  PinkCherry (adult-trained painter) and Heretic (abliterated reader) ship as
  ready-to-install component/profile recipes (PLAN §11.1–11.2), post-v1.
- 16:9 and/or 9:16 — two full generations, shared upstream.