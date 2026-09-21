# Caption A/B gate result — candidate FAILS, incumbent upgraded in place
# (21 September 2026)

Implements the external agent's evaluation plan on the three-panel set
(failing panel + two explicit panels), incumbent vs
`Qwen3-VL-8B-Instruct-abliterated-v2` Q4_K_M + mmproj-Q8 (mradermacher,
5.03 GB + 752 MB, verified sizes — no 15-byte trap), served on :11435
alongside the incumbent on :11434. Temperature 0, identical audit prompt.

## Results

| check | incumbent (Qwen2.5-VL-7B) | ABL-v2 (Qwen3-VL-8B) |
|---|---|---|
| p015 ORIGINAL: tears invented | **no** | **yes** ("both eyes have tear streaks") |
| p015 ORIGINAL: hand near mouth | **yes** ("hand covering face") | **no** ("no visible hands") |
| p015 ORIGINAL: blonde | **yes** | yes (hedged) |
| p015 descreened-512: eyes/tears/hand | wrong (closed eyes, no hands) | wrong (same class) |
| p026 ORIGINAL: character count | **2 (correct)** | **1 (missed the man)** |
| p024 explicit: refuses/sanitizes | 0 | 0 |
| latency (originals) | 1.3–4.9 s | 2.3–5.7 s |

## Gate verdict: ABL-v2 does NOT pass

- Failed the tear probe on the exact failing panel (invented "tear streaks"
  while simultaneously writing "no visible tears" — self-contradictory).
- Missed the visible hand the incumbent caught.
- Regressed character counting on p026 (2→1, missed the man entirely).

**No swap.** The incumbent Qwen2.5-VL-7B stays as `KOMA_VISION_*`.

## The confound the A/B exposed (more important than the model swap)

The Sep-21 "incumbent invents tears/closed eyes" evidence **does not
reproduce on the original panel** — with the ORIGINAL full-res panel and
today's stricter audit prompt, the incumbent got hand/blonde/no-tears right
("hand covering face", eyes noted closed is still a partial miss on eye
state, but no tears invention). What DID poison the earlier run: our
**descreened 512-crop preprocessing** — bilateral-filter passes wash out
the screentone shading that both models use to infer hair tone and eye
state (ABL-v2 called the same panel's hair "black" on the descreened
version, "blonde" on the original). The descreen step is S2's input
preparation; feeding its output to S1 was our error — S1 should always see
the original panel.

## Actions

1. **Incumbent stays.** ABL-v2 parked as a parked A/B (weights kept on the
   box; `koma-cap-server` task kept for re-testing).
2. **Pipeline fix adopted regardless of model:** S1 caption input is the
   ORIGINAL panel (never the descreened copy). Descreen belongs to S2 only.
3. **Schema contract adopted** (the external agent's architecture change):
   captions are now requested as a numbered fact sheet (character_count,
   eye_state, mouth_state, hands[], hair_color closed-vocab, clothing,
   framing, text_present) — hallucination becomes a measurable field miss.
   Implemented in the caption prompt template; JSON-schema tightening is
   the follow-up.
4. Caption-it (runner-up) remains parked — same envelope, not fetched.

## Reproduce

`/tmp/koma-qual/caption_ab.py` (A/B) + `/tmp/koma-qual/caption-ab/`
(ab_results.json, p015_original.json, p026_original.json).