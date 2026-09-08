"""Steps — A1..C4 pipeline steps as idempotent functions (PLAN §3).

Phase 0 ships the registry seam only; step implementations land per phase:
A1 ingest (Phase 1), A2 order review (Phase 1), A3 age-gate (Phase 2),
A4 script (Phase 2), A5 cards (Phase 2), B1 colorize (Phase 3), B2 keyframe
(Phase 4), B3 i2v (Phase 5), B4 tts (Phase 5+), B5 upscale (Phase 5),
C1 mix / C2 subs / C3 assemble / C4 merge (Phase 6).

Runner contract: every step is idempotent — re-running skips finished work,
state is written to disk BEFORE any side effect.
"""
