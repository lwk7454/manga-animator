# Colored lane qualification — 21 September 2026 (§E composition route)

Closes the "still B&W / no reimagined reconstruction" gap. Both adapter
roles from PLAN §E tested **separately** on the same staging image, on the
3090, through the production transport where applicable.

## Step 1 — ratio-specific colored composition (Qwen-Edit 2509)

Inputs: B&W panel crop (composition evidence) + character color sheet
(palette/identity evidence). Prompt: pull camera back to medium shot,
fill the 864×480 canvas with room environment, remove text/bubbles/borders,
match the sheet's colors.

Result (vision-reviewed): **fully colored**, medium shot with bedroom
environment, blonde + red outfit matched to the sheet, text/borders removed,
blush and identity preserved. One honest deviation: the sheet's red *ribbon*
became a red dress/bow interpretation, and the top differs from the sheet
(the B&W panel's halter top was naked-between-chest-and-pants in the source;
the model dressed it per sheet colors). This is a composition *candidate* —
per D16 it needs your approval before production use.

Deliverable: `data/demo/composition_16x9.png`

## Step 2 — A/B the two adapter roles (same staging image)

| | Leg A: composition as R2V reference | Leg B: composition as I2V first frame |
|---|---|---|
| Route | panel-slot ref on `MiniMaxH3ReferenceToVideo` | `first_frame` on `MiniMaxH3ImageToVideo` |
| Submit | production worker (frozen refs, validate, promote) | direct probe transport |
| Time | 98 s | ~99 s |
| Result | colored throughout, identity stable, bedroom stable, natural motion (glance/blink/blush), one mid-clip eye-shape wobble | colored throughout, identity + composition locked to first frame, gentle blush/smile/hair motion, very stable |

Vision verdict: **Leg B (I2V first frame) is visibly more stable** — the
first frame is pixel-anchored, so composition/colors cannot drift; motion is
slightly more conservative. Leg A gives the model more latitude (hair/body
pose shifts a bit more) but held color and identity fine.

## Adapter-role findings (the plan's §E question, answered)

- `MiniMaxH3ImageToVideo` takes `first_frame`/`last_frame` optional IMAGE
  inputs and **no** `audio_vae`/`ref_images` (R2V-only). The two roles are
  genuinely different node paths — the plan's warning against relabeling
  one as the other is validated on hardware.
- **Route default recommendation**: I2V-first-frame for shots where the
  composition staging image is approved as-is (maximum fidelity to the
  approved frame); R2V-ref for shots where the composition should remain a
  soft guide (e.g. when a later prompt wants camera drift).

## Remaining for the colored lane

- Your approval of the composition aesthetic (D16 gate) — especially the
  outfit interpretation vs the sheet
- Wire the route choice into `shot_ratio_specs.route` handling
  (`composition_assisted` enum already exists) and the worker's gate
- Voice bindings still placeholder (D05 gate)