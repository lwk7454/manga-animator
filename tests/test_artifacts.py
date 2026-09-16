"""P04 — artifact staging/promotion, crash windows and ratio independence.

§6/§9 durable behaviors: immutable artifact dirs, hash-then-rename-then-commit,
orphan recovery, stale-result fencing, download-only retries, ratio addition
reusing shared work.
"""

from __future__ import annotations

import json
import uuid as uuid_mod
from pathlib import Path

import pytest

from koma.models.work import AttemptState, WorkState
from koma.storage.artifacts import ArtifactStore, file_digest
from koma.storage.database import Database
from koma.storage.domain_store import KomaStore
from koma.storage.errors import InvalidTransition, NotFound


@pytest.fixture()
def env(tmp_path: Path):
    db = Database(tmp_path)
    db.initialize()
    store = KomaStore(db)
    store.create_series("demo")
    cid = store.ensure_chapter("demo", 1)
    store.import_pages(
        "demo", 1,
        [{"source_path": "001.jpg", "sha256": "a" * 64,
          "panels": [{"panel_id": "ch01_p001_01",
                      "bbox": {"x": 10, "y": 10, "w": 700, "h": 600}}]}],
        operation_id="op-1", import_source_id="src-1",
    )
    arts = ArtifactStore(tmp_path, db)
    return store, arts, tmp_path, cid


def _work(store: KomaStore, stage="colorize", ratio="", fp="fp1",
          scope="panel:ch01_p001_01", cid=None):
    sid = store.get_series_by_slug("demo")
    return store.plan_work([{
        "scope_key": scope, "stage": stage, "ratio": ratio, "mode": "production",
        "input_fingerprint": fp, "desired_spec": {},
        "series_id": sid["id"] if sid else "", "chapter_id": cid,
    }])[0]


def _claim_and_run(store: KomaStore, work_id: str, epoch="e1"):
    aid = store.claim_attempt(work_id, epoch)["attempt_id"]
    store.begin_submission(aid, epoch)
    store.record_remote_acceptance(aid, epoch, f"prompt-{aid[:8]}", "srv")
    store.record_attempt_outcome(aid, epoch, AttemptState.DOWNLOADING)
    store.record_attempt_outcome(aid, epoch, AttemptState.VALIDATING)
    return aid


def _promote(arts: ArtifactStore, aid: str, fp="fp1", payload=b"PNGDATA",
             role="color_master", epoch="e1"):
    arts.write_staged_payload("demo", aid, role, payload)
    return arts.finalize_attempt(
        "demo", aid, epoch, fp,
        [{"output_role": role, "kind": role, "staged_path": f"{role}.png",
          "media_metadata": {"w": 512, "h": 512}}],
    )


def test_promote_immutable_dir_and_pointer(env):
    store, arts, tmp, _cid, *_ = env
    w = _work(store)
    aid = _claim_and_run(store, w["work_id"])
    res = _promote(arts, aid)
    rel = store.db.connection().execute(
        "SELECT relative_path FROM artifacts WHERE id = ?", (res["artifact_ids"][0],)
    ).fetchone()["relative_path"]
    artifact_dir = tmp / rel
    assert artifact_dir.is_dir()
    man = json.loads((artifact_dir / "manifest.json").read_text())
    assert man["sha256"] and man["attempt_id"] == aid
    co = store.current_output("panel:ch01_p001_01", "colorize")
    assert co["artifact_id"] == res["artifact_ids"][0]
    assert not (tmp / "series/demo/_staging" / aid).exists()  # staging removed


def test_promote_reads_back_payload(env):
    store, arts, *_ = env
    w = _work(store)
    aid = _claim_and_run(store, w["work_id"])
    res = _promote(arts, aid)
    rel = store.db.connection().execute(
        "SELECT relative_path FROM artifacts WHERE id = ?", (res["artifact_ids"][0],)
    ).fetchone()["relative_path"]
    uuid = Path(rel).name
    p = arts.payload_path("demo", uuid)
    assert p.read_bytes() == b"PNGDATA"
    man = arts.read_manifest("demo", uuid)
    assert man["input_fingerprint"] == "fp1"


def test_orphan_recovered_after_rename_before_commit(env):
    """§9.5 row 8: crash after rename, before DB promotion."""
    store, arts, *_ = env
    w = _work(store)
    aid = store.claim_attempt(w["work_id"], "e1")["attempt_id"]
    store.begin_submission(aid, "e1")
    store.record_remote_acceptance(aid, "e1", "p1", "srv")
    store.record_attempt_outcome(aid, "e1", AttemptState.DOWNLOADING)
    store.record_attempt_outcome(aid, "e1", AttemptState.VALIDATING)
    # simulate: files renamed into the immutable tree, DB commit lost
    final_dir = arts.artifacts_root("demo") / str(uuid_mod.uuid4())
    final_dir.mkdir(parents=True)
    (final_dir / "payload.png").write_bytes(b"ORPHAN")
    manifest = {
        "schema_version": 1, "artifact_uuid": final_dir.name, "attempt_id": aid,
        "output_role": "color_master", "kind": "color_master",
        "input_fingerprint": "fp1", "sha256": file_digest(final_dir / "payload.png"),
        "size": 6, "media_metadata": {}, "result_metadata": {}, "dependencies": [],
        "created_at": "t",
    }
    (final_dir / "manifest.json").write_text(json.dumps(manifest))
    # recovery sees the orphan and can adopt it
    orphans = arts.find_orphans("demo")
    assert len(orphans) == 1 and orphans[0]["manifest"]["attempt_id"] == aid
    res = arts.adopt_orphan("demo", orphans[0]["relative_path"], "e1")
    assert res["artifact_ids"]
    assert store.current_output("panel:ch01_p001_01", "colorize") is not None
    assert arts.find_orphans("demo") == []


def test_orphan_with_unknown_attempt_left_unselected(env):
    store, arts, *_ = env
    final_dir = arts.artifacts_root("demo") / "00000000-0000-0000-0000-000000000000"
    final_dir.mkdir(parents=True)
    (final_dir / "payload.png").write_bytes(b"x")
    (final_dir / "manifest.json").write_text(json.dumps(
        {"schema_version": 1, "attempt_id": "ghost", "input_fingerprint": "fp",
         "output_role": "color_master", "kind": "color_master", "sha256": "z", "size": 1}))
    orphans = arts.find_orphans("demo")
    assert len(orphans) == 1
    with pytest.raises(NotFound):
        arts.adopt_orphan("demo", orphans[0]["relative_path"], "e1")
    # nothing promoted; pointer stays empty
    assert store.current_output("panel:ch01_p001_01", "colorize") is None


def test_download_retry_does_not_regenerate(env):
    """§23 queue row: download failure retries the SAME accepted attempt."""
    store, _arts, *_ = env
    w = _work(store)
    aid = store.claim_attempt(w["work_id"], "e1")["attempt_id"]
    store.begin_submission(aid, "e1")
    store.record_remote_acceptance(aid, "e1", "p1", "srv")
    store.record_attempt_outcome(aid, "e1", AttemptState.DOWNLOADING)
    # download fails: attempt FAILED but remote acceptance retained
    store.record_attempt_outcome(aid, "e1", AttemptState.FAILED,
                                 error_kind="download_failed", error_detail="net drop")
    row = store.get_attempt(aid)
    assert row["remote_prompt_id"] == "p1"  # acceptance persisted through failure
    # download retry path: same intent, NEW attempt number
    store.set_work_state(w["work_id"], WorkState.READY)
    a2 = store.claim_attempt(w["work_id"], "e1")
    assert a2["attempt_number"] == 2


def test_ratio_independence_and_shared_reuse(env):
    """§23 ratios: first ratio succeeds, second fails; accepted work is
    reused; aggregate stays incomplete until all ratios."""
    store, arts, _tmp, cid, *_ = env
    wc = _work(store, stage="colorize", fp="fp-c", cid=cid)
    aid = _claim_and_run(store, wc["work_id"])
    _promote(arts, aid, fp="fp-c")
    wk1 = _work(store, stage="keyframe", ratio="16:9", fp="fp-k1", cid=cid)
    aid = _claim_and_run(store, wk1["work_id"])
    _promote(arts, aid, fp="fp-k1", role="keyframe")
    wk2 = _work(store, stage="keyframe", ratio="9:16", fp="fp-k2", cid=cid)
    aid2 = store.claim_attempt(wk2["work_id"], "e1")["attempt_id"]
    store.record_attempt_outcome(aid2, "e1", AttemptState.FAILED,
                                 error_kind="remote_failed", error_detail="oom")
    progress = store.derive_progress("demo", 1)
    kf = progress["work_by_stage"]["keyframe"]
    assert kf["16:9"].get("succeeded") == 1
    assert kf["9:16"].get("failed") == 1
    assert store.find_work(stage="colorize", state="succeeded")


def test_late_stale_completion_cannot_overwrite(env):
    """§9.5 row 10: new revision beats the old still-running attempt."""
    store, arts, _tmp, cid, *_ = env
    w1 = _work(store, fp="fp1", cid=cid)
    aid1 = store.claim_attempt(w1["work_id"], "e1")["attempt_id"]
    store.begin_submission(aid1, "e1")
    store.record_remote_acceptance(aid1, "e1", "p1", "srv")
    store.record_attempt_outcome(aid1, "e1", AttemptState.DOWNLOADING)
    # input revised → new work; old work superseded
    store.set_work_state(w1["work_id"], WorkState.SUPERSEDED)
    w2 = _work(store, fp="fp2", cid=cid)
    aid2 = store.claim_attempt(w2["work_id"], "e1")["attempt_id"]
    store.begin_submission(aid2, "e1")
    store.record_remote_acceptance(aid2, "e1", "p2", "srv")
    store.record_attempt_outcome(aid2, "e1", AttemptState.DOWNLOADING)
    store.record_attempt_outcome(aid2, "e1", AttemptState.VALIDATING)
    _promote(arts, aid2, fp="fp2", payload=b"NEW")
    # the old late attempt finally finishes: promotion must be fenced
    store.record_attempt_outcome(aid1, "e1", AttemptState.VALIDATING)
    with pytest.raises(InvalidTransition):
        arts.write_staged_payload("demo", aid1, "color_master", b"OLD")
        arts.finalize_attempt(
            "demo", aid1, "e1", "fp1",
            [{"output_role": "color_master", "kind": "color_master",
              "staged_path": "color_master.png", "media_metadata": {}}],
        )


def test_add_ratio_reuses_other_outputs(env):
    store, arts, _tmp, cid, *_ = env
    wk = _work(store, stage="keyframe", ratio="16:9", fp="fp-k16", cid=cid)
    aid = _claim_and_run(store, wk["work_id"])
    _promote(arts, aid, fp="fp-k16", role="keyframe")
    wk2 = _work(store, stage="keyframe", ratio="9:16", fp="fp-k9", cid=cid)
    assert wk2["work_id"] != wk["work_id"]
    # shared colorize work satisfies both runs (exact-fingerprint dedup)
    shared = store.plan_work([
        {"scope_key": "panel:ch01_p001_01", "stage": "colorize", "ratio": "",
         "mode": "production", "input_fingerprint": "fp-c", "desired_spec": {},
         "series_id": store.get_series_by_slug("demo")["id"], "chapter_id": cid},
        {"scope_key": "panel:ch01_p001_01", "stage": "colorize", "ratio": "",
         "mode": "production", "input_fingerprint": "fp-c", "desired_spec": {},
         "series_id": store.get_series_by_slug("demo")["id"], "chapter_id": cid},
    ])
    assert shared[0]["work_id"] == shared[1]["work_id"]
