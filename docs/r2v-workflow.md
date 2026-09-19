# Direct manga-panel R2V: test workflows

Prepared 19 September 2026 against application commit `d847c829a27575aebd22eabb47ea15d56cb70a33`.
These workflows implement the new direction: original panel → local reference-to-video,
without colorization, outpainting, generated keyframes or first-frame guide generation.
They are test candidates with verified graph contracts, **not GPU-qualified outputs**.

## What the investigation established

The latest pushed application still has only I2V profiles. `steps/render.py` selects
KEYFRAMED panels, loads `keyframe_<width>x<height>.png`, and submits an
`MiniMaxH3ImageToVideo` graph. No R2V workflow or R2V render path is present in that
commit. The owner's failed R2V attempt therefore cannot be reproduced from this
repository snapshot; its graph, prompt and actual output remain needed.

The following are concrete configuration distinctions to check against that run:

1. **Use the R2V task and matching weights.** Stock `fl2va` and `ref2va` are separate
   checkpoints. Renaming a node in an I2V graph does not convert the weights. These
   candidates use stock `minimax_h3_ref2va_pruned_int8_convrot.safetensors`.
2. **Connect both CLIP and video VAE to the reference node.** In the inspected core
   implementation, a reference without `vae` reaches the text encoder but does not
   create the `minimax_refs` latent blocks used by the diffusion model. That is a
   plausible cause of weak resemblance, not a confirmed diagnosis of the unseen run.
3. **Use the actual dynamic socket and prompt tag.** The API key is
   `ref_images.ref_image_0`; the prompt label for this first connected image is
   `<Picture 1>`. The socket index is zero-based; the prompt label is one-based.
4. **Pair turbo weights, steps and sampler deliberately.** The four-step path uses
   the official ref2v-specific turbo LoRA at strength 1.0. It does not apply an I2V
   turbo LoRA or add another turbo LoRA to an already fused checkpoint.
5. **Avoid inherited first-frame distortion.** The inspected I2V implementation
   stretches its first frame to the output canvas. R2V reference sizing keeps the
   source aspect approximately intact, with model-grid rounding. A portrait panel
   used in a landscape output remains a soft reference; it is not a guaranteed
   exact composition or first-frame copy.
6. **Use valid spatial/temporal settings.** These graphs use dimensions divisible
   by 32 and 124 frames (`17n+5`) at 24 fps. The old I2V planner's 768×432 and
   four-second fallback are not the official R2V template's initial test settings.
   This difference is a configuration concern, not proof of the reported failure.

The old fused graph also retains an upstream reference-output label mentioning SLA
although its SLA node is absent. That label is not evidence of the graph's quality.
Its filename suggests an eight-step turbo merge while the application sets four
steps; without the exact checkpoint's validated recipe, that is another variable
worth isolating. These stock workflows remove that uncertainty for comparison.

## Programmatic workflow for an agent (recommended)

No ComfyUI browser interaction is required. Run the following from the repository
checkout on the Mac/Linux orchestration host, using the project's installed Python.
The new entry point is `python -m koma.r2v_probe`; it is a qualification runner,
not the production chapter scheduler. It requires a running local ComfyUI service,
the matching model assets, Pillow (already a project dependency), and `ffmpeg` plus
`ffprobe` on PATH. It does not install models or start/stop GPU services.

### 1. Check the installed service without generating anything

```sh
python -m koma.r2v_probe check \
  --server http://192.168.50.103:8188 \
  --panel /absolute/path/to/panel.png \
  --variant turbo4 \
  --run-dir data/probes/panel-check-001
```

Use your actual literal LAN IP or localhost. `check` reads `/object_info` and
`/system_stats`, validates the image, required nodes/inputs, link types, model
filename enums and settings, and writes `preflight.json`. It performs no upload,
model load or generation. A matching dropdown entry proves availability by name,
not weight hashes, successful loading, GPU memory fit or output quality.

The default [prompt file](../config/prompts/manga_r2v_probe.txt) describes one small
head movement and a blink. The agent should adapt it to the actual panel, save a
runtime copy and pass `--prompt-file /absolute/path/to/prompt.txt` to both `check`
and `run`. Keep `<Picture 1>` in the prompt. No need to edit workflow JSON.

If the weights are in subfolders, pass `--models /absolute/path/to/models.json`:

```json
{
  "unet": "MiniMax/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
  "clip": "MiniMax/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
  "vae": "MiniMax/minimax_h3_video_vae_fp16.safetensors",
  "lora": "MiniMax/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"
}
```

Use the actual server dropdown paths. Only include overrides needed for that
variant; omit `lora` for baseline20. Basenames must match the selected recipe, so
this cannot silently swap to unrelated/fused weights. On schema/model-validation
failure, inspect the saved `preflight.json` for the installed inputs/model options.

### 2. Run one fast test

Use a **new** run directory; the check directory contains diagnostics only.

```sh
python -m koma.r2v_probe run \
  --server http://192.168.50.103:8188 \
  --panel /absolute/path/to/panel.png \
  --variant turbo4 \
  --run-dir data/probes/panel-turbo-001 \
  --wait-seconds 1800
```

The runner chooses 864×480 or 480×864 from the panel orientation. Override with
`--width` and `--height` together, both multiples of 32. Defaults are `--seed 42`
and `--frames 124`; accepted frame counts are `17n+5` within 124..362. Pass the same
prompt/model override options used for preflight. Run also repeats preflight.

It acquires Koma's host-user command lock, checks the remote queue is empty,
uploads the panel under a unique name, saves immutable request details and POSTs
once to `/prompt`. It then polls that prompt, downloads only the declared SaveVideo
node's output, checks actual dimensions/frame count/fps/no-audio, decodes the full
video, and generates an eight-frame contact sheet. It never auto-retries generation.

The shared lock prevents overlapping cooperating Koma owners. The remote queue
check cannot reserve a GPU against other applications or old commands that do not
use ownership. Prepare the shared GPU through its normal controls; this runner
never kills processes, interrupts the server or unloads another application's model.

### 3. Resume after timeout, interruption or a transient download failure

```sh
python -m koma.r2v_probe resume \
  --run-dir data/probes/panel-turbo-001 \
  --wait-seconds 1800

python -m koma.r2v_probe status \
  --run-dir data/probes/panel-turbo-001
```

Resume uses the saved server, graph, seed and prompt ID. It does not regenerate a
known accepted job. If the process died before dispatch, it resumes preparation;
if it may have died during dispatch, it reconciles the exact graph/request marker
against queue and recent history first. Missing or multiple matches produce
`submission_uncertain`, not a second POST. History retention gaps require review.
Do not edit `run.json` to force a retry or create another run directory to bypass
an uncertain attempt. Keep the evidence and resolve what the server accepted.

Ctrl-C and polling timeout stop the client, **not** the remote inference. Neither
means GPU resources were released. Resume this same directory to finish retrieval.
The runner intentionally provides no server-wide interrupt/free command.

### 4. Inspect evidence and optionally run the control

Every command prints one JSON object to stdout. Relevant outcomes/exit codes:

| Outcome | Exit | Agent action |
|---|---|---|
| `preflight_passed` / `complete` | 0 | Inspect the report or output; visual qualification is still false. |
| `accepted` after wait timeout | 4 | Resume the same directory; the server may still be running. |
| `submission_uncertain` / `held` | 4 | Inspect queue/history evidence; never blindly create another generation. |
| `failed` / `rejected` | 4 | Inspect `history.json` or `run.json`; correct the cause before an explicit new test. |
| `owner_busy` | 5 | Respect the existing owner; do not kill or bypass it. |
| Preflight/argument error | 2 | Fix the stated input/capability issue; use a fresh directory for another check. |
| Ctrl-C | 130 | Remote work may continue; resume the same directory. |

`status` is a read-only snapshot and returns 0 when readable regardless of the
stored outcome: inspect `outcome`, not only its exit code. A transient polling or
download exception returns `error`/4 with the accepted request still saved.

A completed directory contains:

- `preflight.json`: selected installed schemas, model choices and server statistics.
- `prompt.json`: the exact API graph, including fixed seed and unique output prefix.
- `run.json`: input/graph hashes, request ID/state, timestamps and output hash.
- `history.json`: remote completion information and reported execution errors.
- `video.mp4` (or the actual supported container): downloaded and decoded result.
- `ffprobe.json`: measured streams/frame count/fps/dimensions.
- `contact-sheet.png`: eight evenly sampled frames; frame indices are in `run.json`.
  A contact-sheet failure is recorded separately and does not invalidate a decoded clip.

The agent should compare the panel and contact sheet, then inspect the clip for
motion/flicker. Technical completion is not a claim of good animation. Artifacts
remain under gitignored runtime data and should not be committed.

If turbo is poor, run the control explicitly using the same panel/prompt/seed and
canvas, after the prior request is resolved:

```sh
python -m koma.r2v_probe run \
  --server http://192.168.50.103:8188 \
  --panel /absolute/path/to/panel.png \
  --variant baseline20 \
  --run-dir data/probes/panel-baseline-001 \
  --wait-seconds 1800
```

The runner does not automatically spend another twenty-step generation on the
assumption that a technically valid result looks bad. An agent can decide to run
this comparison from the test task and actual output evidence.

### Automated validation

```sh
python -m unittest discover -s tests -p 'test_r2v*.py' -v
```

The probe tests use fake ComfyUI responses for submission loss, reconciliation,
model/schema checks, owner conflict and repeat-safe downloads. A separate case uses
real local FFmpeg to decode synthetic media and create the contact sheet. No test
submits a GPU job; absence of FFmpeg skips that local-media case explicitly.


## Optional ComfyUI browser test

| File | Use |
|---|---|
| [Turbo UI](../config/workflows/minimax_h3_panel_r2v_turbo4.ui.json) | First, fast local test; open this in the ComfyUI canvas. |
| [Baseline UI](../config/workflows/minimax_h3_panel_r2v_baseline20.ui.json) | Twenty-step control without the turbo LoRA if the first result is bad. |
| [Turbo API](../config/workflows/minimax_h3_panel_r2v_turbo4.api.json) | Equivalent API prompt graph for a local `/prompt` client. |
| [Baseline API](../config/workflows/minimax_h3_panel_r2v_baseline20.api.json) | Equivalent control prompt graph. |

Download the **raw** `.ui.json` file and drag it into your local ComfyUI. Do not
submit the UI export directly to `/prompt`; the `.api.json` file is the prompt
object for that endpoint, normally wrapped as `{"prompt": <graph>}` by your client.
The API graph expects `panel.png` in ComfyUI input storage until you replace that
LoadImage value with the actual uploaded filename.

1. Confirm your installed ComfyUI exposes `MiniMaxH3ReferenceToVideo` and supports
   its dynamic reference inputs. If a node/input is missing, stop and capture the
   installed version/schema rather than substituting similarly named nodes.
2. Select the exact model filenames below in their loader nodes. Do not rename an
   incompatible weight file to make a dropdown error disappear.
3. Upload **one cropped panel**, with one clearly visible subject for the first
   test. Do not use an entire multi-panel page. No generated keyframe is needed.
4. Use 864×480 for a landscape source, or swap to 480×864 for a portrait source.
   Those are model-grid canvases, not exact 16:9 delivery dimensions. Start with
   the source orientation to reduce composition changes. Final delivery sizing is
   outside this isolated workflow test.
5. Keep seed 42 fixed, 124 frames, 24 fps, `ref_image_size=match`, `res_multistep`,
   `simple`, and denoise 1.0. The turbo version uses 4 steps; the control uses 20.
6. Edit the prompt to describe the actual panel and one small motion. The supplied
   monochrome prompt is a diagnostic style-preservation test, not a product
   requirement that every future output be black-and-white. Avoid multi-shot
   choreography, dialogue, style embeddings and unrelated character references
   while checking basic fidelity. If the panel has no visible face, replace the
   blink/head-turn instruction with a suitable motion.
7. Queue **one** generation. Keep other GPU consumers paused by their own controls;
   these graphs do not manage the application's lane ownership or resource release.

Outputs save under `ComfyUI/output/koma/` with a variant-specific prefix and no
muxed audio. H3 still samples its joint audio/video latent internally; omitting
`VAEDecodeAudio` avoids audio decoding/muxing, not all internal audio computation.
The run lasts about 5.17 seconds of output; generation wall time remains unmeasured.

## Required local assets

| Loader | File under ComfyUI/models |
|---|---|
| UNETLoader | `diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors` |
| CLIPLoader | `text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` |
| VAELoader | `vae/minimax_h3_video_vae_fp16.safetensors` |
| LoraLoaderModelOnly, turbo only | `loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors` |

These names come from the upstream local R2V template. Obtain matching assets from
its [Comfy-Org model repository](https://huggingface.co/Comfy-Org/MiniMax-H3).
Installed subdirectories can change dropdown paths; choose the actual entry for
the same asset. The existing fused file is not substituted by these workflows.
If only the fused file is installed, report its exact filename and your graph;
that needs a separate matched recipe, not a silent model swap.

Do not assume these weights and the live LoRA path fit the 3090 solely from file
sizes. Reference latents, activations and model patching also consume memory.
Allow the normal local memory manager to unload between stages. Avoid forcing
all loaders resident simultaneously. A memory failure needs the allocation log,
not an arbitrary CPU-only fallback or reduced diffusion quality disguised as success.

## If the turbo output is poor

Run the baseline once with the **same panel, prompt, seed, canvas and duration**.
This is a controlled comparison of base versus turbo recipe; the sample trajectory
will differ, so the outputs will not be pixel-identical.

- Baseline good, turbo bad: investigate the LoRA bytes/version, strength and turbo
  sampling path first. Do not add more prompt complexity.
- Both ignore the panel: inspect the exported queued API graph and verify the image
  link, VAE link, exact model family and `<Picture 1>` tag. Capture `/object_info`
  for the reference and loader nodes.
- Both preserve style but move badly: try a smaller single action and locked camera;
  compare whether anatomy is already ambiguous in the source. This may be a model
  capability limitation, not graph wiring.
- Output looks noisy/corrupt: preserve the console log and model identities. Check
  loader/runtime/quantization compatibility before treating it as a prompt issue.
- Out of memory: report the failing node and resident GPU consumers. `match` already
  avoids the much larger reference-token budget of `max`. Do not switch to `max`
  as the first response to bad quality.

There is deliberately no `MiniMaxH3AddGuide`: a soft R2V reference can regenerate
composition and does not lock frame zero. If exact pose/composition is essential,
we should assess that from your test rather than reintroducing a keyframe pipeline.

## What to send back

Please include the actual failed R2V workflow as a ComfyUI UI export **and**, if
possible, its queued API prompt; a source panel you are comfortable sharing; the
result clip; and the exact prompt, model/LoRA/VAE/encoder filenames, ComfyUI commit,
node versions, steps, seed, dimensions, frame count, runtime and any console error.
Describe “bad” concretely: ignores reference, distorted anatomy, photorealistic
style drift, no motion, flicker, or noisy/black frames. The new result alone cannot
identify a fault in an unseen previous graph.

## Evidence and integration boundary

- [Core R2V/I2V node implementation](https://github.com/Comfy-Org/ComfyUI/blob/6338e4bd428247a4a8843496aa98fb7f2a9d3632/comfy_extras/nodes_minimax_h3.py)
  establishes image/VAE conditioning, reference resizing, tags and frame/grid semantics.
- [Official local R2V template](https://github.com/Comfy-Org/workflow_templates/blob/66abae5205f7c5105281146fa109f7c12801d268/templates/video_minimax_h3_r2v.json)
  establishes the stock model names, ref2v turbo LoRA and sampler graph.
- The imported template's UI node shapes were adapted; the MIT notice is included
  alongside these workflows in `LICENSE-Comfy-Org.txt`.

Six workflow contract tests cover reference conditioning, variant/model pairing,
valid canvas/length/seed, latent decode, UI/API equivalence and full graph reachability.
The programmatic runner additionally checks your installed `/object_info` before
submission. Its recovery/transport tests and real synthetic-media test do not
execute CUDA or prove visual quality. The full application suite is separate.

These files are intentionally standalone qualification workflows. The existing
`koma render` CLI is still I2V-only and must **not** be given these files as if they
were a compatible existing profile. After a successful output test, integrate a
separate `video_r2v` role and direct source-panel artifact dependency into the new
application planner; preserve ownership, approvals and recovery. Do not relabel
raw panels as KEYFRAMED just to pass the old lifecycle checks.
