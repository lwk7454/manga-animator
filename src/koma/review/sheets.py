"""A2 order review — dark-theme HTML contact sheets.

Static HTML + tiny stdlib HTTP server: ``koma review <slug>`` serves
``data/series/<slug>/review/``; the page lists one card per new batch
(pages whose panels are AWAITING_ORDER_REVIEW), each panel outlined +
numbered on the page image, direction shown, low-confidence pages
highlighted, click-to-swap fixes two panel ids. POSTing a certification
flips panels AWAITING_ORDER_REVIEW → ORDERED (store-enforced) and batches
never re-appear once certified.

Old batches are never re-reviewed; the sheet is generated per batch at
review time from current state.
"""

from __future__ import annotations

import base64
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..models.domain import Page, PanelState, panel_key
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
  .low h3::after { content:" ⚠ low confidence"; color:#ffb300; font-size:.8rem; }
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
  const swapPairs = (swaps[page] || []).length === 2 ? [swaps[page]] : [];
  fetch('/certify', { method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ slug:{{slug_json}}, page: page, swaps: swapPairs }) })
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
    for i, p in enumerate(page.panels):
        b = p.bbox
        style = (
            f"left:{b.x / page.width * 100:.2f}%;top:{b.y / page.height * 100:.2f}%;"
            f"width:{b.w / page.width * 100:.2f}%;height:{b.h / page.height * 100:.2f}%;"
        )
        panels.append(
            f'<div class="panel" data-page="{page.page_number}" data-idx="{i}" '
            f'data-order="{p.order}" style="{style}"><span>{p.order}</span></div>'
        )
    lowcls = "low" if low else ""
    return (
        f'<div class="pagecard {lowcls}"><h2>page {page.page_number}</h2>'
        f'<div class="page"><img src="data:image/jpeg;base64,{b64}">{"".join(panels)}</div>'
        f'<button onclick="certify({page.page_number})">certify order</button>'
        f'<span class="done" id="done-{page.page_number}"></span></div>'
    )


def generate_review_page(store: SeriesStore, slug: str, out_path: Path) -> int:
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
        low = {i for i, pn in enumerate(page.panels) if pn.order is None} if not ok else set()
        cards.append(_panel_html(page, okb64, low))
    pages_html = "".join(cards) or "<p>Nothing awaiting order review. 🎉</p>"
    html = (
        _REVIEW_HTML.replace("{{slug}}", slug)
        .replace("{{slug_json}}", json.dumps(slug))
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
    """Serve the review sheet + /certify endpoint until Ctrl-C."""
    review_dir = store.series_dir(slug) / "review"
    sheet = review_dir / "order_review.html"
    n = generate_review_page(store, slug, sheet)

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
            try:
                certify_page(store, slug, int(body["page"]), body.get("swaps") or [])
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


def certify_page(store: SeriesStore, slug: str, page_number: int, swaps: list[list[int]]) -> None:
    """Apply click-to-swap fixes, then flip the page's panels to ORDERED.

    Order of operations matters (idempotency doctrine): write the swapped
    orders ONCE, re-derive panel_ids from final positions, then transition
    each panel via set_panel_state (which re-loads fresh state — a stale
    in-memory copy must never be saved over transitions).
    """
    pages = store.load_pages(slug)
    page = next((p for p in pages if p.page_number == page_number), None)
    if page is None:
        raise ValueError(f"page {page_number} not found")

    for a, b in swaps:
        if not (0 <= a < len(page.panels) and 0 <= b < len(page.panels)):
            raise ValueError(f"swap {a},{b} out of range (page has {len(page.panels)} panels)")
        page.panels[a].order, page.panels[b].order = page.panels[b].order, page.panels[a].order

    # re-derive ids from final reading positions (ids encode position)
    page.panels.sort(key=lambda pn: pn.order or 0)
    for i, pn in enumerate(page.panels, start=1):
        pn.order = i
        pn.panel_id = panel_key(page.chapter, page.page_number, i)

    store.save_pages(slug, pages)  # swapped orders persisted

    for pn in page.panels:  # transitions re-load fresh state from disk
        store.set_panel_state(slug, pn.panel_id, PanelState.ORDERED)
