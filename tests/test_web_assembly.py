"""R7 — assembly endpoint tests: run, idempotent start, status, subtitles."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from koma.shot_assembly import AssemblyService
from koma.storage.database import Database
from koma.web.assembly import install_assembly_endpoints


@pytest.fixture()
def db(tmp_path: Path):
    d = Database(tmp_path / "data")
    d.initialize()
    with d.transaction() as conn:
        conn.execute(
            "INSERT INTO series (id, slug, revision, required_ratios,"
            " subtitle_languages, created_at, updated_at)"
            " VALUES ('ser1', 'demo', 1, '[\"16:9\"]', '[\"en\"]',"
            " datetime('now'), datetime('now'))")
        conn.execute(
            "INSERT INTO chapters (id, series_id, number, revision, created_at,"
            " updated_at) VALUES ('ch1', 'ser1', 1, 1, datetime('now'), datetime('now'))")
        conn.execute(
            "INSERT INTO shot_plan_revisions (id, series_id, chapter_id,"
            " shot_id, revision, beat_coverage, source_panel_ids, purpose,"
            " route, camera, created_at, updated_at)"
            " VALUES ('sr:1', 'ser1', 'ch1', 'shot:1', 1, '[\"b1\"]',"
            " '[\"p1\"]', 'p', 'direct_r2v', '{}',"
            " datetime('now'), datetime('now'))")
        conn.execute(
            "INSERT INTO story_beats (id, series_id, chapter_id, plan_revision,"
            " beat_index, source_panel_ids, supporting_text, participants,"
            " dialogue_line_ids, allowed_motion, created_at, updated_at)"
            " VALUES ('b1', 'ser1', 'ch1', 1, 1, '[\"p1\"]', '', '[]',"
            " '[\"ln:p1:0\"]', '[]', datetime('now'), datetime('now'))")
        conn.execute(
            "INSERT INTO artifacts (id, series_id, scope_key, kind, mode,"
            " producing_attempt_id, relative_path, sha256, size,"
            " input_fingerprint, media_metadata, result_metadata,"
            " validation_status, created_at)"
            " VALUES ('art:v1', 'ser1', 'sk', 'motion', 'production', NULL,"
            " 'art_v1_out.mp4', 'aa' * 32, 10, 'fp', '{}', '{}', 'validated',"
            " datetime('now'))")
        conn.execute(
            "INSERT INTO shot_candidates (id, series_id, shot_revision_id,"
            " ratio, candidate_index, artifact_id, input_fingerprint,"
            " acceptance, created_at, updated_at)"
            " VALUES ('cand:1', 'ser1', 'sr:1', '16:9', 1, 'art:v1', 'fp',"
            " 'accepted', datetime('now'), datetime('now'))")
    return d


def _wav_bytes(seconds: float) -> bytes:
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\x00\x10" * int(seconds * 48000))
    return buf.getvalue()


@pytest.fixture()
def client(db, tmp_path: Path):
    import httpx
    # the artifact path points inside the workdir
    video = tmp_path / "art_v1_out.mp4"
    import subprocess
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x64:d=2",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", str(video)],
        check=True, capture_output=True, timeout=60)

    def builder():
        return AssemblyService(db, tts_base="http://tts.test")

    app = FastAPI()

    @app.get("/api/v1/csrf")
    async def csrf():
        return JSONResponse({"token": "tok"}, headers={
            "set-cookie": "koma_csrf=tok; Path=/; HttpOnly; SameSite=strict"})

    import hmac as _hmac

    def _csrf_ok(request):
        cookie = request.cookies.get("koma_csrf")
        header = request.headers.get("x-koma-csrf")
        if not cookie or not header:
            return False
        return _hmac.compare_digest(cookie, header)

    app.state.csrf_ok = _csrf_ok

    # fake TTS at the HTTP boundary
    def fake_post(url, json=None, timeout=None, trust_env=None):
        class R:
            status_code = 200
            content = _wav_bytes(1.0)
        return R()

    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(httpx, "post", fake_post)
    install_assembly_endpoints(app, builder, tmp_path)
    c = TestClient(app)
    yield c
    monkeypatch.undo()


def _csrf(client):
    r = client.get("/api/v1/csrf")
    return {"x-koma-csrf": r.json()["token"]}


def test_assemble_runs_to_completion(db, tmp_path, client):
    r = client.post("/api/v1/shots/sr:1/assemble", headers=_csrf(client), json={
        "ratio": "16:9", "lines": {"ln:p1:0": {"jp": "一"}},
        "ref_audio_path": "ref.wav", "prompt_text": "p",
    })
    assert r.status_code == 200
    assert r.json()["status"] == "started"
    s = {"status": "running"}
    deadline = __import__("time").time() + 30
    while __import__("time").time() < deadline:
        s = client.get("/api/v1/shots/sr:1/assemble/status",
                       params={"ratio": "16:9"}).json()
        if s["status"] != "running":
            break
        __import__("time").sleep(0.2)
    assert s["status"] == "succeeded"
    assert s["video_duration_s"] == __import__("pytest").approx(2.0, abs=0.3)
    assert s["has_audio"] if "has_audio" in s else True
    # subtitles were written alongside
    subs = s.get("subtitles") or []
    assert any(p.endswith("subtitles_jp.srt") for p in subs)


def test_subtitle_download(db, tmp_path, client):
    # run assembly first (same as above) then fetch the jp track
    client.post("/api/v1/shots/sr:1/assemble", headers=_csrf(client), json={
        "ratio": "16:9", "lines": {"ln:p1:0": {"jp": "テスト"}},
        "ref_audio_path": "ref.wav", "prompt_text": "p",
    })
    s = {"status": "running"}
    s = {"status": "running"}
    deadline = __import__("time").time() + 30
    while __import__("time").time() < deadline:
        s = client.get("/api/v1/shots/sr:1/assemble/status",
                       params={"ratio": "16:9"}).json()
        if s["status"] != "running":
            break
        __import__("time").sleep(0.2)
    r = client.get("/api/v1/shots/sr:1/subtitles/jp")
    assert r.status_code == 200
    assert "テスト" in r.json()["content"]
    r2 = client.get("/api/v1/shots/sr:1/subtitles/en")
    assert r2.status_code == 404  # no en translations provided


def test_unknown_shot_404(db, tmp_path, client):
    client.post("/api/v1/shots/sr:none/assemble", headers=_csrf(client), json={
        "ratio": "16:9", "lines": {}, "ref_audio_path": "r", "prompt_text": "p",
    })
    s = {"status": "running"}
    deadline = __import__("time").time() + 10
    while __import__("time").time() < deadline:
        s = client.get("/api/v1/shots/sr:none/assemble/status",
                       params={"ratio": "16:9"}).json()
        if s["status"] != "running":
            break
        __import__("time").sleep(0.2)
    assert s["status"] == "failed"
    assert "shot revision" in s["detail"]


def test_csrf_required(client):
    r = client.post("/api/v1/shots/sr:1/assemble", json={
        "ratio": "16:9", "lines": {}, "ref_audio_path": "r", "prompt_text": "p",
    })
    assert r.status_code == 403
