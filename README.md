# manga-animator

Manga pages → animated chapter video. `koma` is the CLI.

`DECISIONS.md` records the current constraints and supersedes older statements
in `PLAN.md`. Model research lives under `docs/research/`.

## Status — September 2026

Implemented: JSON series storage, ingest and order review, scripting and
character proposals, profile loading, GPU job planning, and a polling monitor.
These stages remain experimental. Review identity, concurrent persistence,
retries, and per-ratio completion still need coordinated fixes before an
unattended chapter run is dependable.

- All generation requests stay local, including captions, motion text, translations
  and character proposals. LLM and ComfyUI adapters reject public endpoints and
  disable environment proxies and redirects. Use localhost or literal private LAN IPs.
- Automated age scoring and cloud Llama Guard have been removed. `koma gate` is
  a compatibility transition only; scripting can start directly from ORDERED panels.
- Missing required translations leave scripts incomplete. Valid substeps are
  checkpointed and reused on retry when source/model/prompt inputs are unchanged.
- Keyframes default to the series' configured ratios. `--chapter` constrains
  keyframe planning and native-color copying. Render currently requires a separate
  invocation for each ratio; its default remains `16:9`.
- Stock and fused video profiles have offline-tested input bindings. This does
  not establish installed weights, live node compatibility, GPU memory fit, or
  output quality. The outpaint workflow still needs correction and visual validation.
- The monitor waits for its last running job, holds work when the box is known
  offline, and requests a vision handoff only before a new submission. Failed
  handoffs hold the pending job. There is no cross-process GPU lease yet.
- TTS, audio mixing, subtitles, upscale/validation, and chapter assembly are
  unfinished. Model install/default/pin/A-B commands are not implemented.

## Development

Python 3.11+:

```sh
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m ruff check src tests
.venv/bin/koma --help
```

The default test suite uses synthetic data and mocked services and rejects live
socket connections. It does not load model weights or submit GPU jobs. Live OCR
requires the separately installed `manga-ocr` dependency; model provisioning and
Windows custom-node dependencies are separate from the offline test environment.

The September 14 follow-up adds regression coverage for monitor drain, profile
compilation, chapter/ratio selection, canonical candidate labels, component
verification, frame sizing, and converter cycles. Existing job/artifact data is
not migrated by these code changes.

## Local model configuration

Text uses the same local OpenAI-compatible server and model as vision by default,
so a second LLM need not be loaded. Set `[text].api` to `openai` or `ollama` to
match your local server, and `[text].llm` to its installed model name.
`KOMA_TEXT_BASE_URL` and `KOMA_VISION_BASE_URL` override the endpoints;
`KOMA_TEXT_MODEL` overrides the text model. Cloud endpoints are rejected.
Model names and quality still require validation on your hardware. Old cloud
API keys are not inherited by local clients. No endpoint automatically falls
back to a cloud provider.

`script.json` checkpoints contain partial results and an input revision. Failed
translations retry independently; blank or incomplete translation rows do not
count as success. A chapter with unfinished panels is `script_incomplete`.
An interrupted panel may repeat substeps completed since its last checkpoint;
completed panel checkpoints are reusable. Cross-process command ownership and
GPU pause/resume are still pending work. Changing inputs of already-rendered
panels requires the future downstream invalidation implementation.
