# Stage-attribution benchmark — 21 September 2026 (non-explicit, reproducible)

Implements the review directive: establish which stage causes each failure
by tracing the exact prompt entering each model against its output, BEFORE
considering any model swap. Test panel: p015_03 (clean, clothed). All
prompts, payloads and outputs are logged verbatim under
`/tmp/koma-qual/stage-bench/` (S1/S2/S3 json + images + videos).

## S1 — vision captioning (llama Qwen2.5-VL, temperature 0)

Exact prompt logged. Caption of the p015 close-up returned: single
character, head tilted, eyes closed, tears, light hair, dark outfit,
close-up framing. **Errors found:** the girl's eyes are OPEN in the panel
(caption says closed + tears — invented distress), hand-near-mouth pose
omitted, hair actually blonde described as "light-colored". Verdict: the
caption **loses instructions** (pose) and **invents events** (tears) —
this caption feeds downstream prompts, so S1 is a real defect source.
Text-model replacement is justified *at this stage* by this evidence.

## S2 — composition (Qwen-Edit 2509, 7 numbered instructions)

Numbered instruction list logged verbatim. Vision judge scored each
instruction against the output: **7/7 FOLLOWED**, no invented content.
The output (schoolgirl, classroom, camera pulled back, text removed) matches
every instruction. Verdict: **the image-edit model does NOT lose
instructions on this non-explicit panel** — the colored-lane failures on
explicit panels are a content-class behavior, not a prompt-writer problem.

![S2 composition](../data/demo/bench_S2_composition.png)

## S3 — motion (H3), turbo vs baseline20, same prompt + first frame

Frame-difference time series (10-frame buckets, mean |Δ|) — actual timing
evidence, not contact sheets:

| metric | turbo4 | baseline20 |
|---|---|---|
| motion mean (|Δ|) | 3.54 | **4.79** |
| speed variation (p90/p10) | 38.8× (one burst then near-still) | **16.9× (sustained)** |
| wall time | 99 s | 231 s (**2.3×**) |
| profile | burst at t=1.7–3.8 s, still before/after | motion from t=1.2 s to end |

Interpretation: baseline20 produced **35% more total motion, sustained
across the whole clip**; turbo concentrated motion in a middle burst then
went near-still (the "moving layers then freezing" feel). Both clips
respond to the same prompt (wave → head tilt → blinks → pause): the pause
at the end is present in turbo, the baseline keeps moving (a prompt
deviation to note). Cost measured at **2.3×**, not the unmeasured "3×".

Deliverables: `data/demo/bench_S2_composition.png`,
`bench_turbo_motion.mp4`, `bench_b20_motion.mp4`.

## Conclusions

1. **First demonstrated defect is in S1** (vision captions invent events
   and drop facts) — a better text LLM there is evidence-justified, and the
   fix is scoped to the caption stage only.
2. **S2 (image-edit) followed 7/7 instructions** on this panel — no
   evidence against it from non-explicit scenes; the explicit-scene
   sanitize behavior remains a separate, content-dependent case.
3. **S3 motion ceiling is now measured, not assumed**: turbo 3.54 mean vs
   baseline20 4.79, at 2.3× cost — baseline20 is the quality lever, 2.3×
   the cost. The turbo "freeze after burst" profile is visible in the
   bucket series.
4. Contact-sheet judging replaced by frame-difference time series for all
   future motion QA (timing, pauses, acceleration are now measurable).

Reproduce: scripts `stage_bench*.py` in /tmp/koma-qual/ + logged prompts.