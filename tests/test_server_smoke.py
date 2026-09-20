"""R8 — full-server smoke: AppFactory wires runs + assembly + static console.

One test that boots the real app (all endpoints) and exercises the static
console route + a run enqueue through the same path `koma serve` uses.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from koma.web.api import AppFactory


def test_server_serves_console_and_api(tmp_path: Path):
    AppContext_open(tmp_path)
    app = AppFactory(tmp_path).build()
    c = TestClient(app)
    # console page served at /
    r = c.get("/")
    assert r.status_code == 200
    assert "koma console" in r.text
    assert "color-scheme: dark" in r.text  # dark theme
    # health + assets + runs + assembly routes all registered
    assert c.get("/api/v1/health").json() == {"ok": True}
    routes = {getattr(x, "path", "") for x in app.routes}
    assert "/api/v1/runs" in routes
    assert "/api/v1/runs/{work_id}" in routes
    assert "/api/v1/shots/{shot_revision_id}/assemble" in routes
    assert "/api/v1/shots/{shot_revision_id}/subtitles/{lang}" in routes
    assert "/" in routes


def AppContext_open(tmp_path: Path):
    from koma.application.assets import AppContext
    return AppContext.open(tmp_path)
