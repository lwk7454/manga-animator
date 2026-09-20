# manga-animator

> **Current status (20 September 2026):** the standalone R2V probe is usable;
> the local console and shot assembly are partial workflow components. Colored
> reference generation and scene reconstruction are not implemented. Production
> web generation requires an explicitly configured transport; assembly is an
> explicit preview until approved script/voice and chapter-export integration land.
> See the review checkpoint in [PLAN.md](PLAN.md) for milestone status.


Manga pages → animated chapter video. `koma` is the Python CLI.

[PLAN.md](PLAN.md) is the detailed implementation handoff and acceptance contract.
[DECISIONS.md](DECISIONS.md) records settled owner decisions.
[Model guidance](docs/models.md) distinguishes shipped configuration from live qualification.

## Direct-panel R2V — current direction

The owner has replaced the colorize/keyframe pipeline with direct panel-to-video
R2V. Start with the [local R2V test workflows](docs/r2v-workflow.md): a four-step
turbo graph and a twenty-step control, with importable ComfyUI UI/API files.
Run them without the browser using `python -m koma.r2v_probe run --help`; the
[agent instructions](docs/r2v-workflow.md#programmatic-workflow-for-an-agent-recommended)
cover preflight, submission, resume and evidence capture. They are structurally
tested, not hardware-qualified. The existing `render` CLI
still uses the old I2V/keyframe path; integration awaits a successful output test.

## Earlier implementation status

The owner now reports generated-video tests with poor results. Earlier
no-run statements describe the September 15 baseline, not current runtime state.
Do not delete newer runtime work or treat old offline status as live acceptance.

**Implemented (16 Sep 2026, offline-tested at `ef01272`; 275 tests + lint):**
transactional SQLite store with work/attempt lifecycle and crash recovery
(P01), host-wide command ownership with takeover approval (P02), revisioned
order review preserving panel identity (P03), immutable artifacts with
staging→hash→rename→promotion and orphan recovery (P04), managed resource
lanes with verified pause/release (P05), typed script substeps with
line-level checkpoints and D12 translation completeness (P06), registry
lifecycle with non-circular sheet approval chains (P07), profile pins +
verification-vs-qualification gates (P08), full-source fit/pad/mask and
exact frame plans (P09 offline), TTS seam + sample-exact timelines (P10),
real ffmpeg mixer with BGM resting gain and ducking (P11), ffprobe
validation + encoder policy + exact assembly manifests (P12 offline).

**Not yet implemented:** unified `run` command over the new machinery (P13),
release cleanup (P14), subtitle artifacts (§18.2), upscale seam wiring.
**Not qualified (hardware-gated, §24):** every live model/service/encoder
path — the 3090 box is reserved by the owner; qualification records exist
and await real probe evidence.

Still true from earlier iterations: local-only endpoints (no cloud, no
proxies), automated scoring removed, translations block completion, monitor
drains its last running job, `pause --now` is not implemented.

PLAN.md defines those targets; commands mentioned there may be future commands.
Use `koma --help` for the current command surface. Do not run overlapping pipeline
commands until ownership is implemented.

## Development

Python 3.11 or newer, from the repository root:

```sh
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m ruff check src tests
.venv/bin/koma --help
```

Tests are organized by feature; reusable synthetic inputs and fake local
providers live in `tests/helpers.py`. The default suite rejects live socket
connections and does not load weights or submit GPU jobs. Hardware qualification
and future real-ffmpeg integration remain separate acceptance gates in PLAN.md.

`make venv install` is the alternative persistent-uv-environment installation;
`make test`, `make lint`, `make health` and `make doctor` use that environment.
`make clean` removes known build/test caches, preserving source and runtime data.
The editable source checkout is currently required for bundled configuration.

## Local configuration

`config/config.toml` contains example LAN settings; match them to your machine.
Paths resolve relative to the repository/package root, not the current directory.
Runtime data defaults to gitignored `data/`; secrets belong in environment variables.

Text defaults to the same local OpenAI-compatible server/model as vision. Set
`[text].api` to `openai` or `ollama` for the selected local service.
`KOMA_TEXT_BASE_URL`, `KOMA_VISION_BASE_URL` and `KOMA_TEXT_MODEL` override routing.
Cloud keys are not inherited and no cloud fallback exists. The shipped model
names are configuration candidates, not evidence that weights are installed.

Live OCR additionally requires `manga-ocr`; GPU services, model provisioning and
Windows custom-node dependencies are separate from the offline Python setup.
`koma health` checks local binaries; `koma doctor --help` describes the current
service check. Neither is the complete future qualification procedure.

## Repository maintenance

Commit production code, maintained configuration/docs and durable feature tests.
Keep generated media, model weights, database files, probe logs, scraped research,
review reports and scratch scripts outside version control. Historical research
and superseded decisions remain available in Git history; they do not override
the current plan or constitute live validation.

