"""R6 — motion review tests: candidates, exclusive acceptance, budget."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from koma.motion_review import MotionReviewService
from koma.storage.database import Database
from koma.storage.errors import BlockedPrerequisite, InvalidTransition, NotFound


@pytest.fixture()
def review(tmp_path: Path):
    db = Database(tmp_path)
    db.initialize()
    with db.transaction() as conn:
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
            "INSERT INTO shot_ratio_specs (id, shot_revision_id, ratio,"
            " target_width, target_height, placement, framing_note,"
            " reference_manifest, generation_profile, dependency_fingerprint,"
            " created_at)"
            " VALUES ('spec:1', 'sr:1', '16:9', 864, 480, '{}', 'n', '[]', NULL,"
            " 'fp', datetime('now'))")
    return MotionReviewService(db), db


def _art(db, artifact_id="art:c1"):
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO artifacts (id, series_id, scope_key, kind, mode,"
            " producing_attempt_id, relative_path, sha256, size,"
            " input_fingerprint, media_metadata, result_metadata,"
            " validation_status, created_at)"
            " VALUES (?, 'ser1', 'sk', 'motion', 'production', NULL, ?,"
            " 'aa' * 32, 10, 'fp', '{}', '{}', 'validated', datetime('now'))",
            (artifact_id, f"{artifact_id}/out.mp4"),
        )


def test_register_candidates_increasing_index(review):
    svc, db = review
    _art(db, "art:c1")
    _art(db, "art:c2")
    c1 = svc.register_candidate(shot_revision_id="sr:1", series_id="ser1",
                                ratio="16:9", artifact_id="art:c1",
                                input_fingerprint="fp:1")
    c2 = svc.register_candidate(shot_revision_id="sr:1", series_id="ser1",
                                ratio="16:9", artifact_id="art:c2",
                                input_fingerprint="fp:1")
    assert (c1["candidate_index"], c2["candidate_index"]) == (1, 2)


def test_budget_exhaustion_blocks_registration(review):
    svc, db = review
    for i in range(4):
        aid = f"art:b{i}"
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO artifacts (id, series_id, scope_key, kind, mode,"
                " producing_attempt_id, relative_path, sha256, size,"
                " input_fingerprint, media_metadata, result_metadata,"
                " validation_status, created_at)"
                " VALUES (?, 'ser1', 'sk', 'motion', 'production', NULL, ?,"
                " 'aa' * 32, 10, 'fp', '{}', '{}', 'validated', datetime('now'))",
                (aid, f"{aid}/out.mp4"),
            )
        svc.register_candidate(shot_revision_id="sr:1", series_id="ser1",
                              ratio="16:9", artifact_id=aid,
                              input_fingerprint="fp:1")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO artifacts (id, series_id, scope_key, kind, mode,"
            " producing_attempt_id, relative_path, sha256, size,"
            " input_fingerprint, media_metadata, result_metadata,"
            " validation_status, created_at)"
            " VALUES ('art:b4', 'ser1', 'sk', 'motion', 'production', NULL,"
            " 'art:b4/out.mp4', 'aa' * 32, 10, 'fp', '{}', '{}', 'validated',"
            " datetime('now'))")
    with pytest.raises(BlockedPrerequisite) as ei:
        svc.register_candidate(shot_revision_id="sr:1", series_id="ser1",
                              ratio="16:9", artifact_id="art:b4",
                              input_fingerprint="fp:1")
    assert "budget exhausted" in str(ei.value)


def test_accept_is_exclusive_and_supersedes_siblings(review):
    svc, db = review
    for i in (1, 2):
        aid = f"art:s{i}"
        _art(db, aid)
        svc.register_candidate(shot_revision_id="sr:1", series_id="ser1",
                              ratio="16:9", artifact_id=aid,
                              input_fingerprint="fp:1")
    pending = svc.pending_for_shot("sr:1", "16:9")
    res = svc.accept_candidate(pending[1]["id"], reviewer="pooh")
    assert res["acceptance"] == "accepted"
    siblings = svc.pending_for_shot("sr:1", "16:9")
    assert siblings[0]["acceptance"] == "superseded"
    assert siblings[1]["acceptance"] == "accepted"
    # accepting a superseded candidate is illegal
    with pytest.raises(InvalidTransition):
        svc.accept_candidate(pending[0]["id"], reviewer="pooh")


def test_reject_requires_quality_reason(review):
    svc, db = review
    _art(db, "art:r1")
    c = svc.register_candidate(shot_revision_id="sr:1", series_id="ser1",
                               ratio="16:9", artifact_id="art:r1",
                               input_fingerprint="fp:1")
    with pytest.raises(ValueError):
        svc.reject_candidate(c["candidate_id"], reviewer="pooh", note="  ")
    svc.reject_candidate(c["candidate_id"], reviewer="pooh",
                         note="eye drift destroyed the ahegao")
    pending = svc.pending_for_shot("sr:1", "16:9")
    assert pending[0]["acceptance"] == "rejected"
    assert "ahegao" in pending[0]["rejection_note"]


def test_retry_blocked_while_unreviewed_or_exhausted(review):
    svc, db = review
    _art(db, "art:t1")
    c = svc.register_candidate(shot_revision_id="sr:1", series_id="ser1",
                               ratio="16:9", artifact_id="art:t1",
                               input_fingerprint="fp:1")
    # unreviewed candidate blocks retry (review-first, not retry-first)
    with pytest.raises(BlockedPrerequisite):
        svc.next_retry_spec("sr:1", "16:9", seed=99)
    svc.reject_candidate(c["candidate_id"], reviewer="pooh", note="bad")
    # budget left: 3 of 4 → retry allowed
    spec = svc.next_retry_spec("sr:1", "16:9", seed=99)
    assert spec == {"retry": True, "seed": 99}


def test_budget_override_via_profile(review):
    svc, db = review
    with db.transaction() as conn:
        conn.execute(
            "UPDATE shot_ratio_specs SET generation_profile = ? WHERE id = 'spec:1'",
            (json.dumps({"candidate_budget": 2}),),
        )
    for i in (1, 2):
        aid = f"art:o{i}"
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO artifacts (id, series_id, scope_key, kind, mode,"
                " producing_attempt_id, relative_path, sha256, size,"
                " input_fingerprint, media_metadata, result_metadata,"
                " validation_status, created_at)"
                f" VALUES ('{aid}', 'ser1', 'sk', 'motion', 'production', NULL,"
                f" '{aid}/out.mp4', 'aa' * 32, 10, 'fp', '{{}}', '{{}}',"
                " 'validated', datetime('now'))")
        svc.register_candidate(shot_revision_id="sr:1", series_id="ser1",
                              ratio="16:9", artifact_id=aid,
                              input_fingerprint="fp:1")
    assert svc.retry_budget_left("sr:1", "16:9") == 0
    with pytest.raises(BlockedPrerequisite):
        svc.next_retry_spec("sr:1", "16:9", seed=1)


def test_unknown_candidate_404(review):
    svc, _db = review
    with pytest.raises(NotFound):
        svc.reject_candidate("candidate:none", reviewer="pooh", note="x")
