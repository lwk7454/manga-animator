# Gaps G1–G7 implementation (21 Sep 2026)

Software wiring only. Live 3090 pixel / motion qualification of the new
production path is still an owner hardware step.

## Shipped

- **G1** colored R2V: `appearance_evidence` role, recolor-first prompt
  contract (`r2v_prompt.py`), Qwen-Edit sheet proposal (`application/sheets.py`).
  B&W-preserving language is rejected (PLAN line 75).
- **G2** composition-assisted: exclusive `composition_r2v_ref` vs
  `composition_i2v_first_frame`. Default adapter role is I2V first-frame
  (Leg B, most stable on 21 Sep hardware). Worker + transport gates lifted
  only when the exclusive role is set. Roles are never relabelled.
- **G3** serve orchestration: `plan_chapter_work` / `compile_colored_work`
  create frozen motion work from the shot plan. `POST /api/v1/runs`
  compiles when no pending work exists, then executes through the configured
  `KOMA_COMFY_BASE` transport (`AppFactory` already built `build_worker`).
- **G4** fragment grouping: `scenes.pick_primary_panel` selects the largest
  crop; planner emits that panel first so composition re-imagines from the
  most complete explicit source.
- **G5** 9:16 delivery of a 3:4 recolor is a center-crop (`delivery.py`).
  Qwen-Edit vertical extension remains rejected.
- **G6** chapter assembly: beat-ordered concat, offset SRT, integer-frame
  assembly manifest. Production voice bindings remain an owner gate (D05);
  this unit stitches already-assembled shot outputs.
- **G7** caption model is unchanged. Incumbent Qwen2.5-VL-7B stays
  `KOMA_VISION_*`. A swap still requires the tear/hand + fact P/R gate on
  ≥30 held-out original panels (never descreened-512).

## Motion QA

`motion_qa.frame_diff_series` is the production measurement. Contact sheets
are not evidence.
