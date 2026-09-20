"""R8 — static UI (dark theme): one-page workflow console over /api/v1.

Single self-contained page served at /: assets approval screen, shot plan
review, motion candidates, assembly. Vanilla JS (no build step), dark theme
per house style. All mutations carry the CSRF token; same-origin only.
"""

from __future__ import annotations

from pathlib import Path

_INDEX = Path(__file__).resolve().parent / "static" / "index.html"


def install_static(app) -> None:
    from fastapi.responses import HTMLResponse

    @app.get("/", include_in_schema=False)
    async def index():
        return HTMLResponse(_INDEX.read_text(encoding="utf-8"))


__all__ = ["install_static"]
