# G1 hardware verification — production colored R2V (21 Sep 2026, night)

Implements the owner directive: **direct panel R2V must output colored
video** (PLAN §A Output + §E line 185). Verified through the REAL project
flow — no ad-hoc graphs:

```
compile_colored_work (sr:1, 16:9)
  manifest: ref_image_0 = panel (story_composition, <Picture 1>)
            ref_image_1 = approved sheet (appearance_evidence, <Picture 2>)
  prompt:   build_colored_r2v_prompt — recolor per sheet, never B&W
            (assert_colored_prompt rejects monochrome-preserving language)
  → store.plan_work (fingerprint 064e8be0…)
→ build_worker(db, :8188) → R2VWorker.execute(ShotRatioJob)
  → ComfyR2VTransport.prepare/submit/poll/fetch/validate
  → outcome: succeeded (prompt d17ddcb1-066f…)
  → artifact promoted: motion, ffprobe-validated, candidate registered
```

## Pixel evidence

- 864×480, 124 frames @ 24 fps (H.264, validated by the transport)
- mid-frame mean 146.3; **chroma (mean HSV saturation) 107.9** — B&W
  output measures < ~10; this is decisively colored
- vision review: riding pose preserved from the panel, girl blonde per
  the approved sheet, explicit content uncensored; nits: face partially
  out of frame at 16:9, minor hand anatomy

## The five call sites from the external agent's report, wired + tested

1. `r2v_worker._check_production` → `production_gate.check_production_route`
2. `ComfyR2VTransport.prepare` → accepts composition I2V first-frame
   graphs, manifest from `spec["reference_manifest"]` or ratio spec
3. `web/runs` enqueue → `plan_chapter_work` when no work rows exist
4. `planner._emit` → `order_source_panels` (primary = largest crop)
5. `r2v_compiler` → skips `composition_i2v_first_frame` slots in R2V refs

Also fixed from the external push: center-crop test math (test asserted
the impossible x=0/w=480 for 480×640→9:16; correct is w=356 centered),
production-gate test aligned to the real contract (route stays
`composition_assisted` in schema; the exclusive adapter role is the gate
argument), lint clean. Suite: **451 passed + 6 subtests**.

Deliverables: `data/demo/g1_colored_r2v.mp4`, `g1_colored_mid.png`.
Scripts: g1_seed/g1_compile/g1_execute.py in /tmp/koma-qual/.