# Production test — improved pipeline, fresh explicit panel (p028_02)
# 21 September 2026, evening run

Improvements applied (from the caption gate + stage benchmark):
1. S1 captions the ORIGINAL panel (descreened-512 poisoning eliminated)
2. Fact-sheet caption contract (numbered fields, measurable misses)
3. Descreen is S2-only preparation
4. Recolor-first composition at native aspect
5. Reimagined choreography motion prompt
6. Frame-difference time series as motion QA

## Results

S1 fact sheet (original panel, temp 0): 6 fields, anatomically explicit
("chest, nipples, abdomen, pubic area"), zero invented events.

S2 recolor (448×448 native, fact-sheet prompt): genitals exposed and
uncensored exactly as drawn, diamond text frames removed, composition
unchanged, clean tan shading. Vision-verified.

S3 motion (I2V native-aspect, choreography prompt: breathing, muscle
flex, thrust, sway, sweat): motion mean **7.85** vs stage-bench turbo
**3.54** (2.2×), speed variation **1.3×** vs **38.8×** — sustained motion
across the full clip, no freeze-after-burst. The choreography prompt +
native-aspect I2V + no-descreen-for-S1 improvements compound.

Deliverables: data/demo/prod_improved_input.png (S2 input),
prod_improved_comp.png (colored), prod_improved_final.mp4 (video).
Scripts: prod_test_s1/s2/s2b/s3/motion.py in /tmp/koma-qual/.