# manga-animator

Manga pages → animated chapter video. `koma` is the Python CLI.

[PLAN.md](PLAN.md) is the detailed implementation handoff and acceptance contract.
[DECISIONS.md](DECISIONS.md) records settled owner decisions.
[Model guidance](docs/models.md) distinguishes shipped configuration from live qualification.

## Current status

The system has never been run end to end. There is no accepted generated work
to migrate. Historical run counts, timings and installation claims are not
acceptance evidence.

Implemented experimental foundations: JSON series storage, panel detection and
order review, local scripting and character proposals, declarative workflow
profiles, GPU job planning and a polling monitor. None of this establishes a
fully working video generator.

- All generation stays local, including captions, translations and character
  proposals. LLM/ComfyUI clients reject public endpoints and disable environment
  proxies and redirects. Use localhost or a literal private LAN IP.
- Automated scoring and cloud Llama Guard are removed. `gate` is a compatibility
  transition; scripting accepts ORDERED panels directly.
- Required translations block script completion. Valid panel checkpoints are
  reused when their source/model/prompt inputs are unchanged. An interrupted
  panel can repeat work performed since its last checkpoint.
- Keyframes use the configured series ratios unless selected explicitly.
  Render still requires a separate invocation for each ratio (default `16:9`).
- The monitor drains its last running job and holds known-offline work. Its
  current pause flag does not provide verified GPU release or command ownership;
  `pause --now` is not implemented.

Still pending: transactional metadata/queue, stable review identity, append-safe
imports, independent ratio completion/retries, durable submission recovery,
approved reference bindings, safe command takeover and resource-releasing
pause/resume. TTS, measured mixing, subtitles, upscale, media validation, chapter
assembly/merge and a unified `run` command are unfinished. The outpaint graph
requires correction. Installed model compatibility, speed, memory use and output
quality require real local probes.

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
