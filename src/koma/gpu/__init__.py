"""GPU — ComfyUI worker + scheduling primitives (Phases 3-5).

Planned contents:
- ``ComfyUIWorker`` — submit/poll/download/free_vram against the box's
  ComfyUI API; poll-"nowhere" ⇒ FAILED (vidgen decision #32).
- ``sizing`` — audio-first length sizing on the live-read 17n+5 frame grid
  (never hardcoded; vidgen decision #38; never pad/speed video).
- boot state machine via SSH (KOMA_GPU_* env; idle-gated, machine-off ≡
  paused per PLAN §9).
- Per-job unique output prefixes AND upload names (vidgen decisions #11/#40).

Phase 0 ships no implementations.
"""
