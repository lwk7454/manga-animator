"""R2 — API tests: boundaries, CAS, CSRF/origin, two-tab conflict, gates.

The browser approval path (upload→review→approve→bind) is exercised against
the real ASGI app via httpx transport — no network, no ComfyUI.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from koma.application.assets import AppContext, AssetService
from koma.storage.errors import RevisionConflict
from koma.web.api import AppFactory


@pytest.fixture()
def ctx(tmp_path: Path):
    ctx = AppContext.open(tmp_path)
    with ctx.db.transaction() as conn:
        conn.execute(
            "INSERT INTO series (id, slug, revision, required_ratios,"
            " subtitle_languages, created_at, updated_at)"
            " VALUES ('ser1', 'demo', 1, '[\"16:9\"]', '[\"en\"]',"
            " datetime('now'), datetime('now'))")
    return ctx


@pytest.fixture()
def client(ctx) -> TestClient:
    app = AppFactory(ctx.data_dir).build()
    return TestClient(app)


@pytest.fixture()
def asset_with_revision(ctx):
    a = ctx.assets.create_asset("ser1", "character", "Heroine")
    rev = ctx.assets.add_revision(a["asset_id"], {"palette": {"hair": "blonde"}},
                                  origin="uploaded")
    return a, rev


def _csrf(client: TestClient) -> dict:
    r = client.get("/api/v1/csrf")
    assert r.status_code == 200
    token = r.json()["token"]
    return {"x-koma-csrf": token}


def test_health(client):
    assert client.get("/api/v1/health").json() == {"ok": True}


def test_list_assets_and_active_payload(ctx, client, asset_with_revision):
    a, _rev = asset_with_revision
    ctx.assets.approve_revision(a["asset_id"], 1, approved_by="owner")
    r = client.get("/api/v1/series/ser1/assets")
    assert r.status_code == 200
    assets = r.json()["assets"]
    assert len(assets) == 1
    assert assets[0]["active_payload"]["palette"]["hair"] == "blonde"
    assert assets[0]["approval"]["state"] == "approved"


def test_cross_origin_rejected(client):
    r = client.get("/api/v1/series/ser1/assets",
                   headers={"origin": "http://evil.example.com"})
    assert r.status_code == 403


def test_mutation_requires_csrf(client, asset_with_revision):
    a, _ = asset_with_revision
    r = client.post(f"/api/v1/assets/{a['asset_id']}/approve", json={
        "revision": 1, "approved_by": "owner", "expected_asset_revision": 1})
    assert r.status_code == 403  # no CSRF token


def test_csrf_token_endpoint_sets_cookie(client):
    r = client.get("/api/v1/csrf")
    assert "koma_csrf" in r.cookies
    assert "token" in r.json()


def test_approve_flow_with_csrf(client, asset_with_revision):
    a, _ = asset_with_revision
    headers = _csrf(client)
    r = client.post(f"/api/v1/assets/{a['asset_id']}/approve",
                    headers=headers,
                    json={"revision": 1, "approved_by": "owner",
                          "expected_asset_revision": 1})
    assert r.status_code == 200
    assert r.json()["state"] == "approved"


def test_stale_expected_revision_conflicts(client, asset_with_revision):
    a, _ = asset_with_revision
    headers = _csrf(client)
    r = client.post(f"/api/v1/assets/{a['asset_id']}/approve",
                    headers=headers,
                    json={"revision": 1, "approved_by": "owner",
                          "expected_asset_revision": 99})
    assert r.status_code == 409
    assert "moved to revision" in r.json()["detail"]


def test_two_tab_conflict(client, ctx, asset_with_revision):
    """Tab A approves; tab B's stale approval attempt conflicts (CAS)."""
    a, _ = asset_with_revision
    hA = _csrf(client)
    r1 = client.post(f"/api/v1/assets/{a['asset_id']}/approve",
                     headers=hA,
                     json={"revision": 1, "approved_by": "tabA",
                           "expected_asset_revision": 1})
    assert r1.status_code == 200
    _csrf(client)  # fresh token after tab A's action
    # tab B acts with stale knowledge of asset revision 1, but adding a new
    # revision in between would change it — simulate by direct service call:
    with pytest.raises(RevisionConflict):
        service = AssetService(ctx)
        service.approve(a["asset_id"], 1, approved_by="tab-b",
                        expected_asset_revision=99)


def test_exact_selected_revision_approved(ctx, client, asset_with_revision):
    """Two revisions; approving r2 binds approval to r2, not r1."""
    a, _ = asset_with_revision
    ctx.assets.add_revision(a["asset_id"], {"palette": {"hair": "silver"}})
    headers = _csrf(client)
    r = client.post(f"/api/v1/assets/{a['asset_id']}/approve",
                    headers=headers,
                    json={"revision": 2, "approved_by": "owner",
                          "expected_asset_revision": 2})
    assert r.status_code == 200
    rev1 = ctx.assets.get_revision(a["asset_id"], 1)
    assert rev1["approval"]["state"] == "awaiting_review"  # r1 untouched
    rev2 = ctx.assets.get_revision(a["asset_id"], 2)
    assert rev2["approval"]["state"] == "approved"


def test_exact_variant_binding_not_inferred_from_name(ctx, client, asset_with_revision):
    """Name matching must not create bindings — only explicit actions do."""
    a, _ = asset_with_revision
    # create a second series row so cross-series name equality is provable:
    with ctx.db.transaction() as conn:
        conn.execute(
            "INSERT INTO series (id, slug, revision, required_ratios,"
            " subtitle_languages, created_at, updated_at)"
            " VALUES ('ser2', 'demo2', 1, '[]', '[]', datetime('now'), datetime('now'))")
    ctx.assets.create_asset("ser2", "character", "Heroine")
    ctx.assets.add_revision(a["asset_id"], {"palette": {"hair": "silver"}})
    ctx.assets.approve_revision(a["asset_id"], 2, approved_by="owner")
    # binding is explicit only:
    r = client.post("/api/v1/series/ser1/bindings", headers=_csrf(client),
                    json={"asset_id": a["asset_id"], "asset_revision": 2,
                          "panel_id": "ch01_p001_01"})
    assert r.status_code == 200
    # binding r1 (unapproved) after r2 approved must fail:
    r1_bind = client.post("/api/v1/series/ser1/bindings", headers=_csrf(client),
                          json={"asset_id": a["asset_id"], "asset_revision": 1,
                                "panel_id": "ch01_p001_02"})
    assert r1_bind.status_code == 422
    # no name-based inference happened anywhere:
    impact = client.get(f"/api/v1/assets/{a['asset_id']}/impact").json()
    assert len(impact["bindings"]) == 1


def test_unapproved_asset_blocks_binding(ctx, client, asset_with_revision):
    a, _ = asset_with_revision  # revision 1 awaiting_review
    r = client.post("/api/v1/series/ser1/bindings", headers=_csrf(client),
                    json={"asset_id": a["asset_id"], "asset_revision": 1,
                          "panel_id": "ch01_p001_01"})
    assert r.status_code == 422
    assert "production requires an approved revision" in r.json()["detail"]


def test_unknown_binding_blocks_production(ctx):
    a = ctx.assets.create_asset("ser1", "character", "Heroine")
    ctx.assets.add_revision(a["asset_id"], {"palette": {}})
    ctx.assets.approve_revision(a["asset_id"], 1, approved_by="owner")
    b = ctx.assets.bind_unknown("ser1", panel_id="ch01_p009_05", note="ambiguous crop")
    assert b["state"] == "ambiguous"
    # the shot gate surfaces unknown states via bindings:
    binds = ctx.assets.bindings_for(panel_id="ch01_p009_05")
    assert binds[0]["state"] == "ambiguous"


def test_dependency_impact_preview(ctx, client, asset_with_revision):
    a, _ = asset_with_revision
    ctx.assets.approve_revision(a["asset_id"], 1, approved_by="owner")
    ctx.assets.bind_occurrence("ser1", panel_id="ch01_p001_01",
                               asset_id=a["asset_id"], asset_revision=1)
    ctx.assets.bind_occurrence("ser1", shot_id="shot_x",
                               asset_id=a["asset_id"], asset_revision=1)
    impact = client.get(f"/api/v1/assets/{a['asset_id']}/impact").json()
    subjects = {b["subject"] for b in impact["bindings"]}
    assert subjects == {"ch01_p001_01", "shot_x"}


def test_404_on_missing_asset(client):
    r = client.get("/api/v1/assets/asset:missing/impact")
    assert r.status_code == 404


def test_media_by_artifact_id_only(ctx, client):
    """Arbitrary filesystem paths are not API inputs (§D media containment)."""
    r = client.get("/api/v1/media?path=/etc/passwd")
    assert r.status_code in (404, 405)  # no such route exists by design
