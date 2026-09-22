"""A2 order review — dark-theme HTML contact sheets (revisioned, D03-safe).

Static HTML + stdlib HTTP server: ``koma review <slug>`` serves
``data/series/<slug>/review/``; the page lists one card per batch awaiting
review, each panel outlined + numbered on the page image, direction shown,
low-confidence pages highlighted, click-to-swap fixes reading order.

POSTing a certification submits the FULL desired panel order (§12: never
just the last swapped pair) with the page's expected order-review revision
and an idempotent operation id. Panel IDs are IMMUTABLE — sorting changes
reading_order only, never identity (D03/B07). Batches never re-appear once
certified; the sheet is regenerated from current state.

Loopback-only server with a per-session token check: state-changing POSTs
must carry the token issued at server start (§12 operational integrity).
"""

from __future__ import annotations

import base64
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..models.domain import Page, PanelState
from ..storage.series_store import SeriesStore

_REVIEW_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>koma review — {{slug}}</title>
<style>
  body { background:#141414; color:#e6e6e6; font-family:-apple-system,sans-serif; margin:2rem; }
  h1 { font-size:1.3rem; } h2 { font-size:1.05rem; color:#9ad1ff; margin-top:2rem; }
  .page { position:relative; display:inline-block; margin:1rem; border:1px solid #333; }
  .page img { display:block; max-width:640px; height:auto; opacity:.92; }
  .panel { position:absolute; border:2px solid #4caf50; color:#4caf50; font-weight:700;
           font-size:14px; cursor:pointer; user-select:none; }
  .panel span { background:#141414cc; padding:0 4px; }
  .low .panel { border-color:#ffb300; color:#ffb300; }
  .low h2::after { content:" ⚠ low confidence"; color:#ffb300; font-size:.8rem; }
  .done { color:#4caf50; margin-top:.5rem; display:none; }
  .dir { color:#888; font-size:.85rem; }
  button { background:#2d5b8e; color:white; border:0; padding:.5rem 1rem;
           border-radius:4px; cursor:pointer; margin-top:.5rem; }
</style></head><body>
<h1>koma review — {{slug}} ({{direction}})</h1>
<p class="dir">Reading direction: <b>{{direction_label}}</b>. Click two panel numbers to swap.</p>
{{pages_html}}
<script>
const swaps = {};
document.querySelectorAll('.panel').forEach(el => {
  el.addEventListener('click', () => {
    const page = el.dataset.page, idx = el.dataset.idx;
    swaps[page] = swaps[page] || [];
    el.style.borderColor = '#ff5252'; el.style.color = '#ff5252';
    if (swaps[page].length === 2) {
      const [a, b] = swaps[page];
      const pa = document.querySelector(`.panel[data-page="${page}"][data-idx="${a}"]`);
      const pb = document.querySelector(`.panel[data-page="${page}"][data-idx="${b}"]`);
      const oa = pa.dataset.order, ob = pb.dataset.order;
      pa.dataset.order = ob; pb.dataset.order = oa;
      pa.querySelector('span').textContent = ob; pb.querySelector('span').textContent = oa;
      pa.style.borderColor = '#4caf50'; pa.style.color = '#4caf50';
      pb.style.borderColor = '#4caf50'; pb.style.color = '#4caf50';
      swaps[page] = [];
    } else { swaps[page].push(idx); }
  });
});
function certify(page) {
  // FULL desired order derived from the CURRENT DOM state (§12), not indices.
  const panels = [...document.querySelectorAll(`.panel[data-page="${page}"]`)];
  const order = panels.sort((x, y) => +x.dataset.order - +y.dataset.order)
                      .map(p => p.dataset.pid);
  fetch('/certify', { method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ token:{{token_json}}, page: page, panel_ids: order }) })
    .then(r => r.json()).then(d => {
      const el = document.getElementById('done-' + page);
      const show = (ok, msg) => { el.style.display = 'inline'; if (!ok) el.style.color = '#f66';
                                  el.textContent = msg; };
      d.ok ? show(true, '✅ certified') : show(false, 'error: ' + d.error);
    });
}
</script></body></html>"""


def _panel_html(page: Page, b64: str, low: set[int]) -> str:
    panels = []
    ordered = sorted(enumerate(page.panels), key=lambda t: t[1].order or 0)
    for display_pos, (idx, p) in enumerate(ordered, start=1):
        b = p.bbox
        style = (
            f"left:{b.x / page.width * 100:.2f}%;top:{b.y / page.height * 100:.2f}%;"
            f"width:{b.w / page.width * 100:.2f}%;height:{b.h / page.height * 100:.2f}%;"
        )
        panels.append(
            f'<div class="panel" data-page="{page.page_number}" data-idx="{idx}" '
            f'data-pid="{p.panel_id}" data-order="{display_pos}" '
            f'style="{style}"><span>{display_pos}</span></div>'
        )
    lowcls = "low" if low else ""
    return (
        f'<div class="pagecard {lowcls}"><h2>page {page.page_number}</h2>'
        f'<div class="page"><img src="data:image/jpeg;base64,{b64}">{"".join(panels)}</div>'
        f'<button onclick="certify({page.page_number})">certify order</button>'
        f'<span class="done" id="done-{page.page_number}"></span></div>'
    )


def generate_review_page(
    store: SeriesStore, slug: str, out_path: Path, token: str | None = None
) -> int:
    """Render the current review sheet; returns number of pages awaiting review."""
    import cv2

    pages = [p for p in store.load_pages(slug)
             if any(pn.state is PanelState.AWAITING_ORDER_REVIEW for pn in p.panels)]
    meta = store.load_series(slug).meta
    direction_label = (
        "right-to-left (JP manga)" if meta.direction.value in ("rtl", "auto") else "left-to-right"
    )

    cards = []
    for page in pages:
        img_path = store.series_dir(slug) / "source" / page.source_path
        img = cv2.imread(str(img_path))
        ok = img is not None
        h, w = (img.shape[:2] if ok else (page.height, page.width))
        if ok:
            scale = 640 / max(w, h)
            if scale < 1:
                img = cv2.resize(img, (int(w * scale), int(h * scale)))
            okb64 = base64.b64encode(
                cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])[1]
            ).decode()
        else:
            okb64 = ""
        low = {i for i, pn in enumerate(page.panels) if pn.needs_review or pn.order is None}
        cards.append(_panel_html(page, okb64, low))
        if any(pn.needs_review for pn in page.panels):
            cards.append("<p>Crop masks/ownership need review. Use the local mask checkout, "
                         "submit and approve commands before certifying order.</p>")
    pages_html = "".join(cards) or "<p>Nothing awaiting order review. 🎉</p>"
    html = (
        _REVIEW_HTML.replace("{{slug}}", slug)
        .replace("{{slug_json}}", json.dumps(slug))
        .replace("{{token_json}}", json.dumps(token or ""))
        .replace("{{direction}}", meta.direction.value)
        .replace("{{direction_label}}", direction_label)
        .replace("{{pages_html}}", pages_html)
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return len(pages)


def serve_review(
    store: SeriesStore, slug: str, port: int = 8618, open_browser: bool = True
) -> None:
    """Serve the review sheet + /certify endpoint until Ctrl-C.

    Loopback-only bind + per-session token: every state-changing POST must
    present the token rendered into the page (unrelated pages cannot issue
    certified orders by accident — §12).
    """
    review_dir = store.series_dir(slug) / "review"
    sheet = review_dir / "order_review.html"
    token = secrets.token_urlsafe(16)
    n = generate_review_page(store, slug, sheet, token=token)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path in ("/", "/index.html", f"/{sheet.name}"):
                data = sheet.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            if self.path != "/certify":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if body.get("token") != token:
                data = json.dumps({"ok": False, "error": "invalid review session token"}).encode()
                self.send_response(403)
            else:
                try:
                    _certify_by_ids(
                        store, slug, int(body["page"]), list(body.get("panel_ids") or [])
                    )
                    payload = {"ok": True}
                except Exception as e:
                    payload = {"ok": False, "error": str(e)}
                data = json.dumps(payload).encode()
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args: object) -> None:  # quiet
            del fmt, args

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    typer_echo = f"review: {n} page(s) awaiting certification → http://127.0.0.1:{port}"
    print(typer_echo)
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nreview server stopped")


def _certify_by_ids(
    store: SeriesStore, slug: str, page_number: int, desired_panel_ids: list[str]
) -> dict:
    """Submit the FULL desired permutation through the transactional store (§12).

    Panel identity never changes (D03): the store updates reading_order only.
    On success every panel transitions AWAITING_ORDER_REVIEW → ORDERED.
    ``page_id`` for the interim store is the chapter-scoped source_path.
    """
    pages = store.load_pages(slug)
    page = next((p for p in pages if p.page_number == page_number), None)
    if page is None:
        raise ValueError(f"page {page_number} not found")
    from ..storage.crop_store import CropStore
    from ..storage.domain_store import KomaStore

    db_store = KomaStore.open(store.data_dir)
    try:
        CropStore(db_store.db).require_approved([pn.panel_id for pn in page.panels])
        canonical = next((p for p in db_store.load_pages(slug, page.chapter)
                          if p["source_path"] == page.source_path), None)
        if canonical:
            row = CropStore(db_store.db).latest(canonical["id"])
            if row and json.loads(row["payload"])["reading_order"] != desired_panel_ids:
                from ..application.crops import replay_page
                from ..crops import Annotation

                annotation = Annotation.model_validate_json(row["payload"])
                annotation.reading_order = desired_panel_ids
                # This endpoint is an explicit human order-certification action.
                revision = CropStore(db_store.db).submit(
                    canonical["id"], annotation, expected_revision=row["revision"]
                )
                CropStore(db_store.db).approve(
                    canonical["id"], expected_revision=revision, actor="order-review"
                )
                replay_page(db_store, slug, page.chapter, canonical["id"])
    finally:
        db_store.db.close()
    expected_rev = store.page_order_revision(slug, page.chapter, page_number)
    op = (
        f"order:{slug}:{page.chapter}:{page_number}:{expected_rev}:"
        f"{hash(tuple(desired_panel_ids)) & 0xFFFFFFFF:x}"
    )
    result = store.set_page_order(
        slug, page.source_path, expected_rev, desired_panel_ids, op
    )
    if not result.get("idempotent"):
        for pn in sorted(page.panels, key=lambda p: p.order or 0):
            store.set_panel_state(slug, pn.panel_id, PanelState.ORDERED)
    return result


def certify_page(store: SeriesStore, slug: str, page_number: int, swaps: list[list[int]]) -> None:
    """Headless certification: apply index swaps, then submit the full order.

    Kept for the CLI ``koma certify`` path. The swaps are applied in memory
    to DERIVE the desired permutation; the persisted change is a full-order
    submission through the transactional store (idempotent operation key).
    Panel IDs are never rewritten (D03/B07).
    """
    pages = store.load_pages(slug)
    page = next((p for p in pages if p.page_number == page_number), None)
    if page is None:
        raise ValueError(f"page {page_number} not found")

    view = list(page.panels)  # index → panel, in stored (display) order
    for a, b in swaps:
        if not (0 <= a < len(view) and 0 <= b < len(view)):
            raise ValueError(f"swap {a},{b} out of range (page has {len(view)} panels)")
        view[a].order, view[b].order = view[b].order, view[a].order

    ordered = sorted(view, key=lambda pn: pn.order if pn.order is not None else 1 << 30)
    desired = [pn.panel_id for pn in ordered]
    _certify_by_ids(store, slug, page_number, desired)
