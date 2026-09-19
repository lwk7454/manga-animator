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

## Files and first test

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

Six offline contract tests cover reference conditioning, variant/model pairing,
valid canvas/length/seed, latent decode, UI/API equivalence and full graph reachability.
They do not load ComfyUI, check your installed `/object_info`, execute CUDA or prove
visual quality. The full application's suite was not rerun for these standalone assets.

These files are intentionally standalone qualification workflows. The existing
`koma render` CLI is still I2V-only and must **not** be given these files as if they
were a compatible existing profile. After a successful output test, integrate a
separate `video_r2v` role and direct source-panel artifact dependency into the new
application planner; preserve ownership, approvals and recovery. Do not relabel
raw panels as KEYFRAMED just to pass the old lifecycle checks.
