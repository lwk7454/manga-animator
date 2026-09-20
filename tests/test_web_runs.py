"""R5 — web run endpoints tests: enqueue, idempotent double start, reconcile."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from koma.r2v_worker import R2VWorker, ShotRatioJob
from koma.scheduler.ownership import OwnerLock
from koma.storage.database import Database
from koma.storage.domain_store import KomaStore
from koma.web.runs import RunRegistry, install_run_endpoints


@pytest.fixture()
def env(tmp_path: Path):
    db = Database(tmp_path)
    db.initialize()
    store = KomaStore(db)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO series (id, slug, revision, required_ratios,"
            " subtitle_languages, created_at, updated_at)"
            " VALUES ('ser1', 'demo', 1, '[\"16:9\"]', '[\"en\"]',"
            " datetime('now'), datetime('now'))"
        )
        conn.execute(
            "INSERT INTO chapters (id, series_id, number, revision, created_at,"
            " updated_at) VALUES ('ch1', 'ser1', 1, 1, datetime('now'), datetime('now'))"
        )
    lock = OwnerLock(tmp_path / "lock")
    return store, lock


def _job(scope, ratio, work_id):
    spec = {
        "shot_revision_id": "sr:1",
        "compiled_graph": {"136": {"inputs": {"x": 1}}},
        "upload_plan": [],
        "seed": 1,
        "output_dir": "/tmp/koma-r5-web",
        "compiled_workflow_hash": "h" * 64,
    }
    return ShotRatioJob(
        work_id=work_id,
        scope_key=scope,
        stage="motion",
        ratio=ratio,
        input_fingerprint="fp:1",
        desired_spec=spec,
    )


def _seed_work(store, job: ShotRatioJob):
    from tests.test_r2v_worker import _seed_shot

    _seed_shot(store, job)
    with store.db.transaction() as conn:
        conn.execute(
            "INSERT INTO work_items (id, series_id, chapter_id, scope_key, stage,"
            " ratio, mode, input_fingerprint, desired_spec, state, priority,"
            " created_at, updated_at)"
            " VALUES (?, 'ser1', 'ch1', ?, ?, ?, 'production', ?, ?, 'ready',"
            " 100, datetime('now'), datetime('now'))",
            (
                job.work_id,
                job.scope_key,
                job.stage,
                job.ratio,
                job.input_fingerprint,
                _dumps(job.desired_spec),
            ),
        )


def _dumps(o):
    import json

    return json.dumps(o)


def _make_client(env, *, submit="pid-ok", poll="generated"):
    store, lock = env

    def builder():
        return R2VWorker(
            store,
            lock,
            owner_label="test-worker",
            submitter=lambda g, up, context: submit,
            poller=lambda pid: poll,
            validator=lambda arts, spec: arts,
            fetcher=lambda pid, d: [
                {
                    "kind": "motion",
                    "relative_path": f"{pid}/out.mp4",
                    "sha256": "cc" * 32,
                    "size": 5,
                    "media_metadata": {},
                    "result_metadata": {},
                }
            ],
        )

    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()

    @app.get("/api/v1/csrf")
    async def csrf():
        return JSONResponse(
            {"token": "test-token"},
            headers={"set-cookie": "koma_csrf=test-token; Path=/; HttpOnly; SameSite=strict"},
        )

    # the CSRF helper state: reuse token match
    import hmac as _hmac

    def _csrf_ok(request):
        cookie = request.cookies.get("koma_csrf")
        header = request.headers.get("x-koma-csrf")
        if not cookie or not header:
            return False
        return _hmac.compare_digest(cookie, header)

    app.state.csrf_ok = _csrf_ok
    registry = RunRegistry()
    install_run_endpoints(app, builder, registry)
    return TestClient(app, base_url="http://127.0.0.1"), registry


def _csrf(client):
    r = client.get("/api/v1/csrf")
    return {"x-koma-csrf": r.json()["token"]}  # cookie persists in the jar


def test_enqueue_runs_to_completion(env):
    store, _ = env
    job = _job("ser1/ch1/shot/s1", "16:9", "w:1")
    _seed_work(store, job)
    client, registry = _make_client(env)
    r = client.post(
        "/api/v1/runs", headers=_csrf(client), json={"chapter_id": "ch1", "ratios": ["16:9"]}
    )
    assert r.status_code == 200
    assert r.json()["enqueued"][0]["status"] in ("started", "already_running")
    final = registry.wait_for("w:1", timeout=10)
    assert final["status"] == "succeeded"
    assert final["artifact_ids"]


def test_enqueue_idempotent_while_running(env):
    store, _ = env
    job = _job("ser1/ch1/shot/s1", "16:9", "w:1")
    job.desired_spec.update(wait_seconds=0.2, poll_seconds=0.01)
    _seed_work(store, job)
    client, _registry = _make_client(env, poll="generating")
    r1 = client.post(
        "/api/v1/runs", headers=_csrf(client), json={"chapter_id": "ch1", "ratios": ["16:9"]}
    )
    assert r1.json()["enqueued"][0]["status"] == "started"
    # browser refresh / double-click: same work returns already_running
    r2 = client.post(
        "/api/v1/runs", headers=_csrf(client), json={"chapter_id": "ch1", "ratios": ["16:9"]}
    )
    assert r2.status_code == 200
    assert r2.json()["enqueued"][0]["status"] == "already_running"
    # GET status reflects running
    s = client.get("/api/v1/runs/w:1").json()
    assert s["status"] == "running"
    _registry.wait_for("w:1", timeout=2)


def test_enqueue_404_without_work(env):
    client, _ = _make_client(env)
    r = client.post(
        "/api/v1/runs", headers=_csrf(client), json={"chapter_id": "ch1", "ratios": ["16:9"]}
    )
    assert r.status_code == 404


def test_csrf_required_for_enqueue(env):
    client, _ = _make_client(env)
    r = client.post("/api/v1/runs", json={"chapter_id": "ch1", "ratios": ["16:9"]})
    assert r.status_code == 403


def test_reconcile_endpoint(env):
    store, _ = env
    job = _job("ser1/ch1/shot/s1", "16:9", "w:1")
    _seed_work(store, job)
    _client, _registry = _make_client(env)
    # make an attempt uncertain directly through the store
    claim = store.claim_attempt(job.work_id, "epoch-t")
    store.record_attempt_intent(
        claim["attempt_id"],
        "epoch-t",
        request_identity="request-z",
        seed=1,
        upload_plan=[],
        output_prefix="p",
    )
    with store.db.transaction() as conn:
        conn.execute(
            "UPDATE attempts SET server_identity='comfyui' WHERE id=?", (claim["attempt_id"],)
        )
    store.begin_submission(claim["attempt_id"], "epoch-t")
    store.mark_submission_uncertain(claim["attempt_id"], "epoch-t", "dropped")
    # reconcile endpoint: server snapshot has the matching graph
    worker = R2VWorker(store, env[1], submitter=lambda g, up, context: "p")
    import hashlib
    import json

    h = hashlib.sha256(
        json.dumps(job.desired_spec["compiled_graph"], sort_keys=True).encode()
    ).hexdigest()
    with store.db.transaction() as conn:
        conn.execute(
            "UPDATE attempts SET compiled_workflow_hash = ? WHERE id = ?", (h, claim["attempt_id"])
        )
    snap = {
        "pid-z": {
            "graph": job.desired_spec["compiled_graph"],
            "request_identity": "request-z",
            "server_identity": "comfyui",
        }
    }
    worker.set_reconcile_snapshot(snap)

    # reconcile uses the worker built per-request; inject snapshot via builder
    def builder():
        w = R2VWorker(
            store,
            env[1],
            submitter=lambda g, up, context: "p",
            poller=lambda pid: "generated",
            fetcher=lambda pid, d: [],
        )
        w.set_reconcile_snapshot(
            {
                "pid-ok": {
                    "graph": job.desired_spec["compiled_graph"],
                    "request_identity": "request-z",
                    "server_identity": "comfyui",
                }
            }
        )
        return w

    import hmac as _hmac

    from fastapi import FastAPI
    from fastapi.responses import JSONResponse as _JR

    app2 = FastAPI()

    @app2.get("/api/v1/csrf")
    async def csrf2():
        return _JR(
            {"token": "tok2"},
            headers={"set-cookie": "koma_csrf=tok2; Path=/; HttpOnly; SameSite=strict"},
        )

    def _csrf_ok2(request):
        cookie = request.cookies.get("koma_csrf")
        header = request.headers.get("x-koma-csrf")
        if not cookie or not header:
            return False
        return _hmac.compare_digest(cookie, header)

    app2.state.csrf_ok = _csrf_ok2
    install_run_endpoints(app2, builder, _registry)
    c2 = TestClient(app2, base_url="http://127.0.0.1")
    r = c2.post(f"/api/v1/attempts/{claim['attempt_id']}/reconcile", headers=_csrf(c2))
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"
    assert r.json()["prompt_id"] == "pid-ok"


def test_run_status_unknown_work(env):
    client, _ = _make_client(env)
    r = client.get("/api/v1/runs/w:none")
    assert r.status_code == 200
    assert r.json()["status"] == "unknown"
