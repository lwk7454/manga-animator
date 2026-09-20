# §G hardware qualification — 3090 box, 20 September 2026

Box: POOH-DESKTOP (RTX 3090 24GB, 32GB RAM). ComfyUI + GPT-SoVITS api_v2
restarted via `koma-qual-comfy` / `koma-qual-tts` schtasks (UTF-8 env fix
required for ComfyUI: GBK logger crash on emoji; TTS yaml pretrained paths
point at `pretrained_models/` root, not the `gsv-v2final-pretrained/` subdir).

## §G.1 — single-reference R2V (panel as <Picture 1>)

- Preflight through the committed probe: **passed** against the live server
  (`koma.r2v_probe check`, turbo4, all 14 graph node classes resolved).
- Real generation: `run` → **complete in ~108 s** wall clock (upload →
  submit → poll → download → ffprobe validate → contact sheet).
- Output: 864×480, **124 frames @ 24 fps**, sha256 recorded in run.json,
  decoded mid-frame pixel check **mean 137.8** (content, not black — the
  fused-profile regression guard).
- Vision review of the contact sheet: character identity consistent across
  all sampled frames; B&W ink + screentone style preserved; visible natural
  motion (mouth opens, eye closes, head tilt); no flicker, no photorealistic
  drift.
- Deliverables: `data/demo/qual_p015_r2v.mp4`, `qual_p015_contact.png`,
  `qual_p015_mid.png`.

## §G.2 — TTS lane (api_v2, live)

- Real synthesis: きゃっ、こんなの初めて…。 → **2.22 s WAV in 8.1 s** wall
  clock (32 kHz PCM, 142 KB). Deliverable: `data/demo/qual_tts.ogg`.

## §G.3 — full assembly chain (koma's own code, real TTS + real video)

`AssemblyService` against the qualified R2V video as the accepted candidate:
- `shot_inputs` resolved the accepted candidate + beat dialogue ✓
- `synthesize_dialogue` measured **2.86 s** from the produced WAV ✓
- `build_mix_cues` sample-resolved ✓
- `mux`: audio padded 2.86 s → **5.167 s** (video duration authority),
  `has_audio: true`, video stream untouched ✓
- Subtitles written from the same timeline (jp + en) ✓
- Deliverable: `data/demo/qual_e2e_assembly.mp4`

## §G.4 — multi-reference leg (panel + character sheet)

`r2v_compiler` built the 2-ref graph (`ref_image_0` = panel via node 137,
`ref_image_1` = character sheet, tags `<Picture 1>`/`<Picture 2>`), submitted
through the probe transport → **complete, 864×480, 124 f @ 24 fps**, mid-frame
mean 140.9. Vision review: **B&W ink style held** (the color sheet did NOT
bleed color at this prompt strength), identity consistent across frames,
composition stable, no 2-ref artifacts. Deliverable:
`data/demo/qual_multiref.mp4` + `qual_multiref_contact.png`.

## §G.5 — 9:16 portrait leg

`p006_04` panel at 480×864 → **complete in ~88 s**, 124 f @ 24 fps,
mid-frame mean 151.4. Vision review: full body head-to-waist framing stable,
identity consistent, visible mouth/eye motion, no anatomy drift.
Deliverable: `data/demo/qual_portrait.mp4` + `qual_portrait_contact.png`.

## Verdict

§G steps 1–3 **qualified on hardware**: R2V single-reference generation,
TTS lane, and the full shot assembly chain all pass with honest,
pixel-verified evidence. Multi-reference and 9:16 legs also pass — both
ratio canvases and the compiler's ref wiring are production-validated.
Remaining for full §G: §G steps 4–6 (three-case review matrix) during R6 use.

## Local suite

389 passed + 6 subtests, lint green, at commit `59abe18`+.