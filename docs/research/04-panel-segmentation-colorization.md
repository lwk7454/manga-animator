# Dossier 4 — Manga panel segmentation, reading order & colorization tooling

Researched 2026-09-08. Web sources (GitHub/HF); to be validated by the
Phase 1 evaluation spike on real pages.

## Panel segmentation + reading order

- **Comic Panel Extractor** (ehsanx — github.com/ehsanx/Comic-Panel-Extractor)
  — open-source panel segmentation + reading-order reconstruction;
  explicitly supports manga/manhwa/webtoon layouts, guesses orientation,
  supports manual overrides. **Primary candidate (Phase 1 spike).**
- **panelforge** (DATGMAC/panelforge) — manga/manhwa panel segmentation +
  reading-order numbering via a tiny specialized U-Net, no pretrained
  backbone. Alternative candidate.
- **manga-panel-recognizer** (erliora) — OpenCV-based panel splitting for
  e-readers; batch processing, debug output, flexible reading order.
  Simpler fallback; OpenCV-only (easy dep).
- Manga109-based datasets/models (MS92/MangaSegmentation; Manga109-panel
  YOLOv2 exports on HF) — research-grade segmentation foundations.
- Max Halforde's comic segmentation writeup + Kumiko project — classic
  Canny/connected-component approaches (older, but the algorithmic
  reference for borderless/nested panel handling).

**Design implication (adopted):** detection is ~solved; the residual risk
is layout weirdness (splash pages, tilted panels, no-border art) — covered
by the per-batch human contact-sheet review (decision #005), so the engine
choice only needs to be "good enough + override-friendly".

## Reading-direction detection

Script-distribution heuristic (adopted): run manga-ocr on page text; JP
vertical text → RTL; hangul → LTR; low text density → gutter analysis
fallback (white gutter on the right side of panels → RTL). Human confirms
per batch via the contact sheet; webtoon long-strip mode skips panel
segmentation entirely (scroll = order).

## Line-art colorization

- **MangaNinja** (johanan528 — "Line Art Colorization with Precise
  Reference Following") — reference-guided line-art colorization with
  fine-grained point matching; derived from diffusion models; handles
  character reference sheets as color sources. **This is the character-
  sheet-anchored colorizer the plan builds on (decision #004/#006).**
- **ComfyUI_MangaNinjia** (smthemex/ComfyUI_MangaNinjia) — ComfyUI node
  pack wrapping MangaNinja → runs on the 3090 alongside the video models
  via the adapter spec (decision #014). Reference points can be specified
  manually (click on reference + line draft) or auto.
- MangaNinja project page: johanan528.github.io/MangaNinjia.

**Unknowns for Phase 3 spike (measure, don't assume):** per-panel
colorization time on the 3090; quality on hentai art (dense screentones,
close-ups); whether auto point-matching suffices or per-panel points are
needed; VRAM alongside the video DiT (colorize lane is separate from the
i2v lane in the scheduler — decision #008 — so co-residency is a
scheduling concern, not a hard conflict).

## OCR

manga-ocr-base (dossier 3) — manga-specialized, handles vertical text /
furigana / overlaid text / low-quality scans; Apache-2.0; runs on the Mac.

## Outpainting (per-ratio keyframes)

Qwen-Image-Edit (decision #011): Apache-2.0, ComfyUI core support,
instruction-following strong, **native Japanese text rendering** — balloons
that survive outpainting. Turbo variants make it near-instant. 3090-fit to
be confirmed in the Phase 4 spike (`koma doctor` + live probe).