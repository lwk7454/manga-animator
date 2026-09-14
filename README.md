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

- Vision runs on the Windows GPU box under decision #024; cloud text and local
  image processing have separate intended roles. Routing enforcement still needs work.
- The gate defaults to pass-through under decision #023. Legacy scoring is opt-in.
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
