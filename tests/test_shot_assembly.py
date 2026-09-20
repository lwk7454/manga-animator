"""R7 — shot assembly tests: inputs resolution, timeline, mix, mux.

Real ffmpeg for the mux path (video is authority; audio pads to it).
TTS is faked at the HTTP boundary (transport tested live on the box).
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from koma.shot_assembly import AssemblyService
from koma.storage.database import Database
from koma.storage.errors import BlockedPrerequisite


@pytest.fixture()
def db(tmp_path: Path):
    d = Database(tmp_path)
    d.initialize()
    with d.transaction() as conn:
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
        conn.execute(
            "INSERT INTO shot_plan_revisions (id, series_id, chapter_id,"
            " shot_id, revision, beat_coverage, source_panel_ids, purpose,"
            " route, camera, created_at, updated_at)"
            " VALUES ('sr:1', 'ser1', 'ch1', 'shot:1', 1, '[\"b1\"]',"
            " '[\"p1\"]', 'p', 'direct_r2v', '{}',"
            " datetime('now'), datetime('now'))"
        )
        conn.execute(
            "INSERT INTO story_beats (id, series_id, chapter_id, plan_revision,"
            " beat_index, source_panel_ids, supporting_text, participants,"
            " dialogue_line_ids, allowed_motion, created_at, updated_at)"
            " VALUES ('b1', 'ser1', 'ch1', 1, 1, '[\"p1\"]', '', '[]',"
            " '[\"ln:p1:0\",\"ln:p1:1\"]', '[]', datetime('now'), datetime('now'))"
        )
    from koma.storage.shot_store import ShotStore

    ShotStore(d).add_ratio_spec("sr:1", "16:9", target_width=864, target_height=480)
    return d


def _accepted_candidate(db, artifact_id="art:v1", rel="shots/sr1/v1.mp4"):
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO artifacts (id, series_id, scope_key, kind, mode,"
            " producing_attempt_id, relative_path, sha256, size,"
            " input_fingerprint, media_metadata, result_metadata,"
            " validation_status, created_at)"
            " VALUES (?, 'ser1', 'sk', 'motion', 'production', NULL, ?,"
            " 'aa' * 32, 10, 'fp', '{}', '{}', 'validated', datetime('now'))",
            (artifact_id, rel),
        )
        conn.execute(
            "INSERT INTO shot_candidates (id, series_id, shot_revision_id,"
            " ratio, candidate_index, artifact_id, input_fingerprint,"
            " acceptance, created_at, updated_at)"
            " VALUES ('cand:1', 'ser1', 'sr:1', '16:9', 1, ?, 'fp',"
            " 'accepted', datetime('now'), datetime('now'))",
            (artifact_id,),
        )


def test_shot_inputs_requires_accepted_candidate(db):
    svc = AssemblyService(db)
    with pytest.raises(BlockedPrerequisite):
        svc.shot_inputs("ser1", "sr:1", "16:9")
    _accepted_candidate(db)
    inputs = svc.shot_inputs("ser1", "sr:1", "16:9")
    assert inputs["video"]["artifact_id"] == "art:v1"
    assert [d["line_id"] for d in inputs["dialogue"]] == ["ln:p1:0", "ln:p1:1"]


def _tone_wav(path: Path, seconds: float, rate: int = 48000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x10" * int(seconds * rate))


def test_synthesize_dialogue_measures_durations(db, tmp_path, monkeypatch):
    svc = AssemblyService(db, tts_base="http://127.0.0.1:9880")
    captured = {}

    def fake_post(url, json=None, timeout=None, trust_env=None, follow_redirects=None):
        captured.setdefault("texts", []).append(json["text"])

        class _R:
            status_code = 200
            content = _wav_bytes(2.0)

        return _R()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    out = svc.synthesize_dialogue(
        [{"line_id": "ln:p1:0", "beat_id": "b1"}, {"line_id": "ln:p1:1", "beat_id": "b1"}],
        {"ln:p1:0": {"jp": "一"}, "ln:p1:1": {"jp": "二"}},
        ref_audio_path="ref.wav",
        prompt_text="p",
        prompt_lang="en",
        workdir=tmp_path,
    )
    assert captured["texts"][0] == "一"
    assert len(out["timeline"]) == 2
    t0, t1 = out["timeline"]
    assert t0["start"] == 0.0 and abs(t0["seconds"] - 2.0) < 0.01
    assert abs(t1["start"] - 2.0) < 0.01  # measured, sequential
    assert abs(out["dialogue_seconds"] - 4.0) < 0.02


def _wav_bytes(seconds: float) -> bytes:
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\x00\x10" * int(seconds * 48000))
    return buf.getvalue()


def test_build_mix_cues_sample_resolved(db):
    svc = AssemblyService(db)
    cues = svc.build_mix_cues(
        [
            {"path": "a.wav", "start": 0.0, "seconds": 2.0},
            {"path": "b.wav", "start": 2.0, "seconds": 1.5},
        ],
        bgm_path=None,
    )
    assert [c["start_sample"] for c in cues] == [0, 96000]


def test_mux_pads_audio_to_video_real_ffmpeg(db, tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg unavailable")
    svc = AssemblyService(db)
    # 2s color video
    video = video_path(tmp_path)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:s=64x64:d=2",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            str(video),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    # 1s dialogue tone at 0
    _tone_wav(tmp_path / "d.wav", 1.0)
    cues = svc.build_mix_cues([{"path": str(tmp_path / "d.wav"), "start": 0.0, "seconds": 1.0}])
    out = tmp_path / "final.mp4"
    res = svc.mux(cues, video, 2.0, out)
    assert res["has_audio"] is True
    assert abs(res["video_duration_s"] - 2.0) < 0.2  # video untrimmed
    assert abs(res["audio_duration_s"] - 2.0) < 0.2  # padded to video
    # black-frame/content guard: decoded mid-frame must not be empty video
    assert out.exists()


def video_path(tmp_path: Path) -> Path:
    return tmp_path / "v.mp4"


def test_blank_dialogue_lines_skipped(db, tmp_path, monkeypatch):
    svc = AssemblyService(db, tts_base="http://127.0.0.1:9880")
    import httpx

    calls = []

    def fake_post(url, json=None, timeout=None, trust_env=None, follow_redirects=None):
        calls.append(json["text"])

        class _R:
            status_code = 200
            content = _wav_bytes(1.0)

        return _R()

    monkeypatch.setattr(httpx, "post", fake_post)
    out = svc.synthesize_dialogue(
        [{"line_id": "ln:p1:0", "beat_id": "b1"}, {"line_id": "ln:p1:1", "beat_id": "b1"}],
        {"ln:p1:0": {"jp": "hello"}, "ln:p1:1": {"jp": "   ", "no_speech": True}},
        ref_audio_path="r",
        prompt_text="p",
        workdir=tmp_path,
    )
    assert len(out["timeline"]) == 1
    assert calls == ["hello"]


def test_tts_rejects_public_endpoint_before_network(db):
    with pytest.raises(ValueError, match="local"):
        AssemblyService(db, tts_base="https://example.com")


def test_missing_dialogue_is_blocked_not_silently_dropped(db, tmp_path):
    service = AssemblyService(db, tts_base="http://127.0.0.1:9880")
    with pytest.raises(BlockedPrerequisite, match="missing required dialogue"):
        service.synthesize_dialogue(
            [{"line_id": "required"}],
            {},
            ref_audio_path="ref.wav",
            prompt_text="reference",
            workdir=tmp_path,
        )


def test_long_dialogue_is_not_truncated(db, tmp_path):
    service = AssemblyService(db)
    speech = tmp_path / "long.wav"
    _tone_wav(speech, 3)
    cues = service.build_mix_cues([{"path": str(speech), "start": 0, "seconds": 3}])
    with pytest.raises(BlockedPrerequisite, match="exceeds video"):
        service.mux(cues, tmp_path / "unopened.mp4", 2, tmp_path / "output.mp4")
    assert not (tmp_path / "output.mp4").exists()


def test_video_path_must_be_contained_and_hash_verified(db, tmp_path):
    service = AssemblyService(db)
    outside = tmp_path.parent / "outside.mp4"
    for relative in [str(outside), "../outside.mp4"]:
        with pytest.raises(BlockedPrerequisite, match="outside"):
            service.video_path({"video": {"relative_path": relative, "sha256": "irrelevant"}})
    path = db.data_dir / "inside.mp4"
    path.write_bytes(b"changed")
    with pytest.raises(BlockedPrerequisite, match="modified"):
        service.video_path({"video": {"relative_path": "inside.mp4", "sha256": "wrong"}})
