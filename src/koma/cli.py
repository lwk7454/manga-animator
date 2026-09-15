"""Current experimental koma command surface.

Use command help for implemented options. PLAN.md §10 specifies the target CLI;
unified run ownership, safe controls and media delivery are still pending."""

from __future__ import annotations

from pathlib import Path

import typer

from . import __version__
from .config import Settings
from .config import settings as default_settings
from .models.domain import PanelState, ReadingDirection
from .storage.series_store import SeriesStore

app = typer.Typer(
    name="koma",
    help="manga-animator — manga pages → coherent animated video.",
    no_args_is_help=True,
    add_completion=False,
)


def _store(data_dir: Path | None = None) -> SeriesStore:
    s: Settings = default_settings()
    return SeriesStore(data_dir if data_dir is not None else s.resolved_data_dir())


def _fail(msg: str) -> None:
    typer.echo(f"error: {msg}", err=True)
    raise typer.Exit(2) from None


@app.command()
def new(
    slug: str = typer.Argument(..., help="Series slug (lowercase, digits, '-' or '_')."),
    direction: str = typer.Option("auto", "--direction", help="rtl | ltr | auto."),
    lang: str = typer.Option("ja", "--lang", help="Source language (ja, ko, ...)."),
    title: str = typer.Option("", "--title", help="Display title (optional)."),
    data_dir: Path = typer.Option(
        None, "--data-dir", hidden=True, help="Override data dir (tests)."
    ),
) -> None:
    """Create a new series store."""
    try:
        rd = ReadingDirection(direction.lower())
    except ValueError:
        _fail(f"invalid --direction {direction!r} (use rtl, ltr or auto)")  # always raises
        raise  # unreachable; satisfies B904
    store = _store(data_dir)
    try:
        store.create_series(slug, title=title, direction=rd, language=lang)
    except (FileExistsError, ValueError) as e:
        _fail(str(e))
    typer.echo(f"created series {slug} ({rd.value}) at {store.series_dir(slug)}")
    typer.echo(f"next: koma ingest {slug} <path> [--chapter N]")


@app.command()
def status(
    slug: str = typer.Argument(...),
    data_dir: Path = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Per-chapter progress + panel lifecycle counts + pause state."""
    store = _store(data_dir)
    if not store.exists(slug):
        _fail(f"no series {slug!r}")
    meta = store.load_series(slug).meta
    typer.echo(f"series: {slug}  direction={meta.direction.value}  lang={meta.language}")
    if meta.video_profile:
        typer.echo(f"video profile: {meta.video_profile} (pinned)")
    typer.echo(f"gpu: {'paused' if store.is_paused() else 'running'} (manual switch)")
    chapters = store.load_chapters(slug)
    if not chapters:
        typer.echo("chapters: none yet")
        return
    pages = store.load_pages(slug)
    for ch in chapters:
        ch_pages = [p for p in pages if p.chapter == ch.chapter_number]
        counts: dict[str, int] = {}
        for pg in ch_pages:
            for pn in pg.panels:
                counts[pn.state.value] = counts.get(pn.state.value, 0) + 1
        detail = " ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "no panels"
        typer.echo(
            f"  ch{ch.chapter_number:02d} [{ch.state}] "
            f"pages={len(ch_pages)} panels: {detail}"
        )


@app.command()
def health(
    data_dir: Path = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Environment check: config, binaries, writability. Informational only."""
    import shutil

    s: Settings = default_settings()
    ok = True

    def report(name: str, good: bool, note: str = "") -> None:
        nonlocal ok
        mark = "✅" if good else "⚠️ "
        if not good:
            ok = False
        typer.echo(f"{mark} {name}{(': ' + note) if note else ''}")

    report("config", s._path.exists(), str(s._path))
    try:
        dd = s.resolved_data_dir() if data_dir is None else data_dir
        dd.mkdir(parents=True, exist_ok=True)
        probe = dd / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        report("data dir writable", True, str(dd))
    except OSError as e:
        report("data dir writable", False, str(e))

    for binary, required in (
        ("ffmpeg", True),
        ("ffprobe", True),
        ("mutool", False),
        ("pdftoppm", False),
    ):
        found = shutil.which(binary) is not None
        note = "found" if found else (
            "MISSING (required)" if required else "not found (optional, for PDF ingest)"
        )
        report(binary, found or not required, note)
    typer.echo("gpu box: use koma doctor for the current service check")
    raise typer.Exit(0 if ok else 1) from None


@app.command()
def pause(
    now: bool = typer.Option(
        False, "--now", help="Immediate interruption is not implemented; requests drain only."
    ),
    data_dir: Path = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Pause GPU work: drain in-flight job, then hold."""
    _store(data_dir).set_paused(True)
    extra = " (--now interruption is not implemented; drain requested only)" if now else ""
    typer.echo("pause requested: scheduler will drain the in-flight job, then hold" + extra)


@app.command()
def resume(data_dir: Path = typer.Option(None, "--data-dir", hidden=True)) -> None:
    """Clear the pause flag; queue continues."""
    _store(data_dir).set_paused(False)
    typer.echo("resumed")


@app.command()
def version() -> None:
    typer.echo(f"koma {__version__}")


@app.command()
def ingest(
    slug: str = typer.Argument(...),
    path: Path = typer.Argument(..., help="JPEG/PNG dir, single image, or PDF."),
    chapter: int = typer.Option(1, "--chapter", help="Chapter number."),
    data_dir: Path = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Import pages → detect panels → propose reading order (A1)."""
    from .steps.ingest import import_chapter

    store = _store(data_dir)
    if not store.exists(slug):
        _fail(f"no series {slug!r}")
    try:
        pages = import_chapter(store, slug, chapter, path)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        _fail(str(e))  # always raises
        raise  # unreachable; satisfies B904
    panels = sum(len(p.panels) for p in pages)
    awaiting = sum(
        1 for p in pages if any(pn.state is PanelState.AWAITING_ORDER_REVIEW for pn in p.panels)
    )
    typer.echo(f"ingested {len(pages)} page(s), {panels} panel(s) into ch{chapter:02d}")
    if awaiting:
        typer.echo(f"next: koma review {slug}  ({awaiting} page(s) awaiting order certification)")


@app.command()
def review(
    slug: str = typer.Argument(...),
    port: int = typer.Option(8618, "--port"),
    no_browser: bool = typer.Option(False, "--no-browser"),
    data_dir: Path = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Serve the dark-theme order-review contact sheet (A2)."""
    from .review.sheets import serve_review

    store = _store(data_dir)
    if not store.exists(slug):
        _fail(f"no series {slug!r}")
    serve_review(store, slug, port=port, open_browser=not no_browser)


@app.command()
def certify(
    slug: str = typer.Argument(...),
    page: int = typer.Option(..., "--page", help="Page number to certify."),
    swap: list[str] = typer.Option(
        [], "--swap", help="Panel idx pair, e.g. --swap 2,4 (repeatable)."
    ),
    data_dir: Path = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Certify a page's panel order headlessly (no browser)."""
    from .review.sheets import certify_page

    store = _store(data_dir)
    try:
        swaps = [[int(a), int(b)] for a, _, b in (s.partition(",") for s in swap)]
        certify_page(store, slug, page, swaps)
    except (ValueError, KeyError) as e:
        _fail(str(e))
    typer.echo(f"page {page} certified")


@app.command()
def gate(
    slug: str = typer.Argument(...),
    chapter: int = typer.Option(1, "--chapter", help="Chapter number."),
    data_dir: Path = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Compatibility command: no scoring. Reviewed panels can be scripted directly."""
    from .steps.gate import gate_series_chapter

    store = _store(data_dir)
    if not store.exists(slug):
        _fail(f"no series {slug!r}")
    gate_series_chapter(store, slug, chapter)
    typer.echo(f"gate skipped: automated scoring removed (ch{chapter:02d})")


@app.command()
def script(
    slug: str = typer.Argument(...),
    chapter: int = typer.Option(1, "--chapter", help="Chapter number."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="No LLM calls — summarize what would run."
    ),
    limit: int = typer.Option(
        None, "--limit", help="Script at most N panels (smoke tests)."
    ),
    data_dir: Path = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Script reviewed panels: captions, OCR, motion, translations (A4)."""
    from .steps.script import script_chapter

    store = _store(data_dir)
    if not store.exists(slug):
        _fail(f"no series {slug!r}")
    if dry_run:
        summary = script_chapter(store, slug, chapter, dry_run=True, limit=limit)
        n = summary["would_script"]
        typer.echo(
            f"dry-run: {n} eligible panel(s) would be scripted (vision → OCR → motion → translate)"
        )
        return
    summary = script_chapter(store, slug, chapter, limit=limit)
    extra = f" → {summary['script']}" if summary.get("script") else ""
    typer.echo(f"scripted {summary['scripted']} panel(s), {summary['failed']} failed{extra}")
    if summary["failed"]:
        typer.echo("failed panels are SCRIPT_FAILED — re-run koma script after fixing the model")
        raise typer.Exit(1) from None


@app.command()
def cards(
    slug: str = typer.Argument(...),
    data_dir: Path = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Show character registry + pending card-review proposals (A5 status)."""
    store = _store(data_dir)
    if not store.exists(slug):
        _fail(f"no series {slug!r}")
    registry = store.load_registry(slug)
    if not registry:
        typer.echo("registry: empty (cards are proposed during koma script)")
        return
    for card in registry:
        mark = {"approved": "✅", "rejected": "❌"}.get(card.review_status, "⏳")
        variants = ", ".join(v.id for v in card.variants) or "none"
        voice = card.voice or "—"
        typer.echo(f"{mark} {card.id} v{card.version} variants: {variants} voice: {voice}")


@app.command()
def discover(
    slug: str = typer.Argument(...),
    chapter: int = typer.Option(None, "--chapter"),
    data_dir: Path = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """A5 discovery: consolidate script captions → pending card proposals."""
    store = _store(data_dir)
    if not store.exists(slug):
        _fail(f"no series {slug!r}")
    s: Settings = default_settings()
    from .steps.cards import discover_cards

    result = discover_cards(store, slug, s, chapter=chapter)
    typer.echo(
        f"proposed {result['proposed']} card(s), skipped {result.get('skipped', 0)}"
        f" (review-gated)"
    )
    if result.get("note"):
        typer.echo(result["note"])


@app.command()
def canonical(
    slug: str = typer.Argument(...),
    data_dir: Path = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Vision-pick each pending card's canonical panel (local vision only)."""
    store = _store(data_dir)
    if not store.exists(slug):
        _fail(f"no series {slug!r}")
    s: Settings = default_settings()
    from .steps.cards import pick_canonical_panels

    picks = pick_canonical_panels(store, slug, s)
    for cid, info in picks.items():
        can = info.get("canonical")
        mark = can or "—"
        note = info.get("note") or info.get("reason", "")[:80]
        typer.echo(f"{cid}: canonical={mark}  ({note})")


@app.command()
def colorize(
    slug: str = typer.Argument(...),
    chapter: int = typer.Option(None, "--chapter", help="Limit to one chapter"),
    data_dir: Path = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Queue colorize jobs for every SCRIPTED panel (Stage B1, GPU lane)."""
    store = _store(data_dir)
    if not store.exists(slug):
        _fail(f"no series {slug!r}")
    s: Settings = default_settings()
    from .steps.colorize import queue_colorize

    result = queue_colorize(slug, store, s, chapter)
    typer.echo(
        f"queued {result['queued']} colorize job(s), skipped {result['skipped']}"
        f" (profile: {result['profile']})"
    )
    typer.echo("run `koma monitor` to process the queue")


@app.command()
def keyframes(
    slug: str = typer.Argument(...),
    chapter: int = typer.Option(None, "--chapter", help="Limit to one chapter."),
    ratio: list[str] = typer.Option(
        [], "--ratio", help="Ratios to build (default: series ratios)."
    ),
    data_dir: Path = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Queue outpaint jobs for every COLORIZED panel (Stage B2, GPU lane)."""
    from .steps.keyframe import queue_keyframes

    store = _store(data_dir)
    if not store.exists(slug):
        _fail(f"no series {slug!r}")
    s: Settings = default_settings()
    ratios = ratio or None
    result = queue_keyframes(slug, store, s, ratios=ratios, chapter=chapter)
    typer.echo(
        f"queued {result['queued']} keyframe job(s), skipped {result['skipped']}"
        f" (ratios: {', '.join(result['ratios'])}; profile: {result['profile']})"
    )
    typer.echo("run `koma monitor` to process the queue")


@app.command()
def render(
    slug: str = typer.Argument(...),
    ratio: str = typer.Option("16:9", "--ratio", help="Target ratio for clips."),
    chapter: int = typer.Option(None, "--chapter", help="Limit to one chapter."),
    data_dir: Path = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Queue i2v jobs for every KEYFRAMED panel (Stage B3, GPU lane)."""
    from .steps.render import queue_render

    store = _store(data_dir)
    if not store.exists(slug):
        _fail(f"no series {slug!r}")
    s: Settings = default_settings()
    result = queue_render(slug, store, s, ratio=ratio, chapter=chapter)
    typer.echo(
        f"queued {result['queued']} render job(s), skipped {result['skipped']}"
        f" (profile: {result['profile']}, ratio: {result['ratio']})"
    )
    typer.echo("run `koma monitor` to process the queue")


@app.command()
def monitor(
    once: bool = typer.Option(False, "--once", help="Single scheduler tick (no loop)"),
    interval: float = typer.Option(10.0, "--interval"),
    slug: str = typer.Option(None, "--slug", help="Series the queue belongs to."),
    data_dir: Path = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Scheduler: submit/poll/download queued GPU jobs (Stage B)."""
    import time as _time

    s: Settings = default_settings()
    dd = data_dir if data_dir is not None else s.resolved_data_dir()
    store = SeriesStore(dd)
    if slug is None:
        # single-series store: infer the slug from the series dir
        sdir = Path(dd) / "series"
        series_dirs = sorted(sdir.glob("*")) if sdir.exists() else []
        if len(series_dirs) == 1:
            slug = series_dirs[0].name

    from .scheduler.monitor import monitor_once

    while True:
        summary = monitor_once(dd, s, store, slug=slug)
        typer.echo(
            f"[tick] submitted={summary['submitted']} downloaded={summary['downloaded']} "
            f"failed={summary['failed']} paused={summary['paused']} "
            f"box={'online' if summary['box_online'] else 'OFFLINE'}"
        )
        if once:
            return
        from .scheduler.queue import load_queue, next_pending, running_job

        q = load_queue(dd)
        if next_pending(q) is None and running_job(q) is None:
            typer.echo("queue drained — monitor exiting")
            return
        _time.sleep(interval)


@app.command()
def doctor(
    data_dir: Path = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Live environment check: box reachability, node IDs, frame grid, packs."""
    s: Settings = default_settings()
    from .gpu.worker import ComfyUIWorker

    worker = ComfyUIWorker(
        host=str(s.get("gpu.host", "192.168.50.103")),
        port=int(str(s.get("gpu.port", 8188))),
    )
    if not worker.is_online():
        _fail(f"box {worker.host}:{worker.port} unreachable; waiting for the local service")

    typer.echo(f"box {worker.host}:{worker.port}: ONLINE")

    # GPU lane arbitration check: both daemons up = violation.
    from .gpu.lanes import check_conflict

    lanes = check_conflict(config=s)
    if lanes["conflict"]:
        typer.echo(f"GPU lanes: {lanes['verdict']}")
    else:
        if lanes["llama_running"]:
            state = "vision up / comfy down"
        elif lanes["comfy_running"]:
            state = "comfy up / vision down"
        else:
            state = "both down (idle)"
        typer.echo(f"GPU lanes: ok ({state})")

    # Verify the colorize profile's nodes + loader widgets live.
    from .adapters import registry

    cdir = s.resolved_config_dir()
    profiles = registry.list_profiles(cdir)
    typer.echo(f"adapter registry: {len(profiles)} profile(s)")
    for p in profiles:
        typer.echo(f"  - {p['id']} ({p['kind']})")

    ok = True
    for p in registry.list_profiles(cdir):
        try:
            resolved = registry.resolve_profile(cdir, p["id"])
        except Exception as exc:
            typer.echo(f"  ✗ {p['id']}: {exc}")
            ok = False
            continue
        inject = resolved["inject"]
        node_ids = {v.split(".")[0] for v in inject.values() if isinstance(v, str) and "." in v}
        missing = [n for n in sorted(node_ids) if n not in resolved["workflow"]]
        if missing:
            typer.echo(f"  ✗ {p['id']}: workflow missing nodes {missing}")
            ok = False
            continue
        # live node-type verification via /object_info
        node_types = set()
        for nid in node_ids:
            entry = resolved["workflow"][nid]
            ctype = entry.get("class_type") or entry.get("type")
            if not ctype:
                typer.echo(f"  ✗ {p['id']}: node {nid} has no class_type/type")
                ok = False
                continue
            node_types.add(ctype)
        info = worker.object_info()
        absent = sorted(t for t in node_types if t not in info)
        if absent:
            typer.echo(f"  ✗ {p['id']}: box lacks node types {absent} (custom pack installed?)")
            ok = False
        else:
            typer.echo(f"  ✓ {p['id']}: all {len(node_types)} node types present on box")
    if not ok:
        raise typer.Exit(1)


models_app = typer.Typer(name="models", help="Model-adapter registry UX.")
app.add_typer(models_app)


@models_app.command("list")
def models_list() -> None:
    """Every role x profile: id, kind, spec path."""
    s: Settings = default_settings()
    from .adapters import registry

    profiles = registry.list_profiles(s.resolved_config_dir())
    if not profiles:
        typer.echo("no profiles registered (config/models/profiles/)")
        return
    for p in profiles:
        typer.echo(f"{p['id']:28s} {p['kind']:12s} {p['path']}")


if __name__ == "__main__":
    app()
