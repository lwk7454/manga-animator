"""R8 — full-server smoke: AppFactory wires runs + assembly + static console.

One test that boots the real app (all endpoints) and exercises the static
console route + a run enqueue through the same path `koma serve` uses.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from koma.web.api import AppFactory


def test_server_serves_console_and_api(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("KOMA_COMFY_BASE", raising=False)
    AppContext_open(tmp_path)
    app = AppFactory(tmp_path).build()
    c = TestClient(app, base_url="http://127.0.0.1")
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
    token = c.get("/api/v1/csrf").json()["token"]
    result = c.post("/api/v1/runs", headers={"x-koma-csrf": token}, json={"chapter_id": "ch1"})
    assert result.status_code == 503  # no silently broken default worker
    assert "innerHTML" not in r.text


def test_configured_server_builds_real_worker_without_contacting_gpu(tmp_path, monkeypatch):
    monkeypatch.setenv("KOMA_COMFY_BASE", "http://127.0.0.1:8188")
    app = AppFactory(tmp_path).build()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        token = client.get("/api/v1/csrf").json()["token"]
        response = client.post(
            "/api/v1/runs", headers={"x-koma-csrf": token}, json={"chapter_id": "missing"}
        )
        assert response.status_code == 404
        assert "no shot plans for chapter/ratios" in response.text


def AppContext_open(tmp_path: Path):
    from koma.application.assets import AppContext

    return AppContext.open(tmp_path)
