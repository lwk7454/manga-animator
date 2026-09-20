"""R1 — asset/shot store feature tests.

§C invariants: explicit-only approvals, exact-revision bindings, revoked
approvals block production, coverage validation (order, duplicates, dialogue),
multi-panel shots and one-panel-many-shots, ratio spec invalidation inputs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from koma.storage.asset_store import AssetStore
from koma.storage.database import Database
from koma.storage.errors import (
    BlockedPrerequisite,
    DuplicateOperation,
    InvalidTransition,
)
from koma.storage.shot_store import ShotStore


@pytest.fixture()
def env(tmp_path: Path):
    db = Database(tmp_path)
    db.initialize()
    assets = AssetStore(db)
    series_id = "ser_test"
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO series (id, slug, revision, required_ratios,"
            " subtitle_languages, created_at, updated_at)"
            " VALUES (?, 'demo', 1, '[\"16:9\"]', '[\"en\"]', datetime('now'), datetime('now'))",
            (series_id,),
        )
        conn.execute(
            "INSERT INTO chapters (id, series_id, number, revision, created_at, updated_at)"
            " VALUES ('ch_test', ?, 1, 1, datetime('now'), datetime('now'))",
            (series_id,),
        )
    from tests.helpers import seed_binding_subjects

    seed_binding_subjects(db, series_id, "ch_test")
    shots = ShotStore(db, assets)
    return db, assets, shots, series_id


def test_create_and_duplicate_asset(env):
    _, assets, _, sid = env
    a = assets.create_asset(sid, "character", "Heroine")
    assert a["asset_id"]
    with pytest.raises(DuplicateOperation):
        assets.create_asset(sid, "character", "Heroine")
    objs = assets.list_assets(sid, kind="object")
    assert objs == []


def test_revision_lifecycle_and_approval_states(env):
    _, assets, _, sid = env
    a = assets.create_asset(sid, "character", "Heroine")
    r1 = assets.add_revision(a["asset_id"], {"palette": {"hair": "blonde"}}, origin="uploaded")
    assert r1["state"] == "awaiting_review"
    # explicit approval only
    res = assets.approve_revision(a["asset_id"], 1, approved_by="owner")
    assert res["state"] == "approved"
    with pytest.raises(InvalidTransition):
        assets.approve_revision(a["asset_id"], 1, approved_by="owner")
    # rejected requires a NEW revision, cannot flip
    assets.add_revision(a["asset_id"], {"palette": {"hair": "silver"}})
    assets.reject_revision(a["asset_id"], 2, note="palette off")
    with pytest.raises(InvalidTransition):
        assets.approve_revision(a["asset_id"], 2, approved_by="owner")
    # revoke only from approved
    with pytest.raises(InvalidTransition):
        assets.revoke_approval(a["asset_id"], 2)


def test_require_approved_blocks_production(env):
    _, assets, _, sid = env
    a = assets.create_asset(sid, "character", "Heroine")
    assets.add_revision(a["asset_id"], {"palette": {}})
    with pytest.raises(BlockedPrerequisite):
        assets.require_approved(a["asset_id"], 1)
    assets.approve_revision(a["asset_id"], 1, approved_by="owner")
    assets.require_approved(a["asset_id"], 1)  # passes


def test_revoked_approval_stales_bindings(env):
    _, assets, _, sid = env
    a = assets.create_asset(sid, "character", "Heroine")
    assets.add_revision(a["asset_id"], {"palette": {}})
    assets.approve_revision(a["asset_id"], 1, approved_by="owner")
    b = assets.bind_occurrence(
        sid, panel_id="ch01_p001_01", asset_id=a["asset_id"], asset_revision=1
    )
    assert b["state"] == "bound"
    bindings = assets.bindings_for(panel_id="ch01_p001_01")
    assert bindings[-1]["approval_state"] == "approved"
    # revoke → binding's recorded revision is no longer approved
    assets.revoke_approval(a["asset_id"], 1, note="palette revision")
    bindings = assets.bindings_for(panel_id="ch01_p001_01")
    assert bindings[-1]["approval_state"] == "superseded"
    with pytest.raises(BlockedPrerequisite):
        assets.require_approved(a["asset_id"], 1)


def test_binding_requires_exactly_one_subject(env):
    _, assets, _, sid = env
    a = assets.create_asset(sid, "character", "Heroine")
    assets.add_revision(a["asset_id"], {"palette": {}})
    assets.approve_revision(a["asset_id"], 1, approved_by="owner")
    with pytest.raises(ValueError):
        assets.bind_occurrence(sid, asset_id=a["asset_id"], asset_revision=1)
    with pytest.raises(ValueError):
        assets.bind_unknown(sid, panel_id="x", shot_id="y")


def test_shot_bindings_use_exact_revisions(env):
    _, assets, _, sid = env
    a = assets.create_asset(sid, "character", "Heroine")
    assets.add_revision(a["asset_id"], {"palette": {"hair": "blonde"}})
    assets.approve_revision(a["asset_id"], 1, approved_by="owner")
    assets.add_revision(a["asset_id"], {"palette": {"hair": "silver"}})
    assets.approve_revision(a["asset_id"], 2, approved_by="owner")
    b1 = assets.bind_occurrence(sid, shot_id="shot_a", asset_id=a["asset_id"], asset_revision=1)
    b2 = assets.bind_occurrence(sid, shot_id="shot_a", asset_id=a["asset_id"], asset_revision=2)
    assert b1["binding_revision"] == 1 and b2["binding_revision"] == 2
    binds = assets.bindings_for(shot_id="shot_a")
    revs = [b["asset_revision"] for b in binds]
    assert revs == [1, 2]  # both retained; exact revisions, no overwrite


def test_beats_and_coverage_validation(env):
    _, _, shots, sid = env
    b1 = shots.add_beat(
        sid,
        "ch_test",
        1,
        beat_index=1,
        source_panel_ids=["ch01_p001_01"],
        dialogue_line_ids=["ln:ch01_p001_01:1"],
    )
    b2 = shots.add_beat(sid, "ch_test", 1, beat_index=2, source_panel_ids=["ch01_p001_02"])
    with pytest.raises(InvalidTransition):
        shots.add_beat(sid, "ch_test", 1, beat_index=2, source_panel_ids=["x"])
    s1 = shots.add_shot_revision(
        sid, "ch_test", beat_coverage=[b1["beat_id"]], source_panel_ids=["ch01_p001_01"]
    )
    s2 = shots.add_shot_revision(
        sid, "ch_test", beat_coverage=[b2["beat_id"]], source_panel_ids=["ch01_p001_02"]
    )
    # one panel -> two shots is NOT a duplicate error; coverage is the authority
    ok = shots.validate_coverage("ch_test", 1, [s1["shot_revision_id"], s2["shot_revision_id"]])
    assert ok["valid"], ok["problems"]
    # missing a beat blocks
    ok2 = shots.validate_coverage("ch_test", 1, [s1["shot_revision_id"]])
    assert not ok2["valid"]
    assert any("uncovered" in p for p in ok2["problems"])


def test_grouped_shot_multi_panel_and_order(env):
    _, _, shots, sid = env
    b1 = shots.add_beat(sid, "ch_test", 1, beat_index=1, source_panel_ids=["p1"])
    b2 = shots.add_beat(sid, "ch_test", 1, beat_index=2, source_panel_ids=["p2"])
    b3 = shots.add_beat(sid, "ch_test", 1, beat_index=3, source_panel_ids=["p3"])
    # several panels -> one shot, beats in order
    g = shots.add_shot_revision(
        sid,
        "ch_test",
        beat_coverage=[b1["beat_id"], b2["beat_id"], b3["beat_id"]],
        source_panel_ids=["p1", "p2", "p3"],
    )
    ok = shots.validate_coverage("ch_test", 1, [g["shot_revision_id"]])
    assert ok["valid"], ok["problems"]
    # grouped shot skipping a beat fails
    skip = shots.add_shot_revision(
        sid,
        "ch_test",
        beat_coverage=[b1["beat_id"], b3["beat_id"]],
        source_panel_ids=["p1", "p3"],
        shot_id="shot_skip",
    )
    ok2 = shots.validate_coverage("ch_test", 1, [skip["shot_revision_id"]])
    # b2 missing → uncovered
    assert not ok2["valid"]


def test_out_of_order_beats_rejected(env):
    _, _, shots, sid = env
    b1 = shots.add_beat(sid, "ch_test", 1, beat_index=1, source_panel_ids=["p1"])
    b2 = shots.add_beat(sid, "ch_test", 1, beat_index=2, source_panel_ids=["p2"])
    g1 = shots.add_shot_revision(
        sid, "ch_test", beat_coverage=[b2["beat_id"]], source_panel_ids=["p2"]
    )
    g2 = shots.add_shot_revision(
        sid, "ch_test", beat_coverage=[b1["beat_id"]], source_panel_ids=["p1"]
    )
    ok = shots.validate_coverage("ch_test", 1, [g1["shot_revision_id"], g2["shot_revision_id"]])
    assert not ok["valid"]
    assert any("order" in p for p in ok["problems"])


def test_ratio_spec_and_production_gate(env):
    db, assets, shots, sid = env
    a = assets.create_asset(sid, "character", "Heroine")
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO artifacts "
            "(id,series_id,scope_key,kind,mode,relative_path,sha256,size,input_fi"
            "ngerprint,validation_status,created_at) "
            "VALUES "
            "('ref1',?,'s','reference','production','ref.png','hash',1,'f','valid"
            "ated',datetime('now'))",
            (sid,),
        )
    assets.add_revision(a["asset_id"], {"palette": {}}, artifact_ids=["ref1"])
    assets.approve_revision(a["asset_id"], 1, approved_by="owner")
    b1 = shots.add_beat(
        sid, "ch_test", 1, beat_index=1, source_panel_ids=["p1"], participants=[a["asset_id"]]
    )
    s1 = shots.add_shot_revision(
        sid, "ch_test", beat_coverage=[b1["beat_id"]], source_panel_ids=["p1"]
    )
    spec = shots.add_ratio_spec(
        s1["shot_revision_id"],
        "16:9",
        target_width=864,
        target_height=480,
        reference_manifest=[
            {
                "slot_key": "ref_image_1",
                "role": "character_identity",
                "entity": a["asset_id"],
                "asset_revision": 1,
                "artifact_id": "ref1",
                "sha256": "hash",
            }
        ],
    )
    assert spec["ratio"] == "16:9"
    with pytest.raises(InvalidTransition):
        shots.add_ratio_spec(s1["shot_revision_id"], "16:9", target_width=864, target_height=480)
    # production-ready: all participants approved + spec exists
    res = shots.require_production_ready(s1["shot_revision_id"], "16:9")
    assert res["ready"]
    # A draft newer design cannot silently rebind this approved shot.
    assets.add_revision(a["asset_id"], {"palette": {"hair": "new"}})
    assert shots.require_production_ready(s1["shot_revision_id"], "16:9")["ready"]
    assets.revoke_approval(a["asset_id"], 1, note="reference withdrawn")
    with pytest.raises(BlockedPrerequisite):
        shots.require_production_ready(s1["shot_revision_id"], "16:9")
    # unapproved participant blocks
    # add an unapproved asset as participant
    a2 = assets.create_asset(sid, "object", "Key Item")
    assets.add_revision(a2["asset_id"], {"silhouette": "x"})
    with db.transaction() as conn:
        conn.execute(
            "UPDATE story_beats SET participants=? WHERE id=?",
            (json.dumps([a2["asset_id"]]), b1["beat_id"]),
        )
    with pytest.raises(BlockedPrerequisite):
        shots.require_production_ready(s1["shot_revision_id"], "16:9")


import json  # noqa: E402  (used in test above)


def test_migration_on_populated_state(tmp_path: Path):
    """v1 store populated → open → v2 migration preserves all rows."""
    db_path = tmp_path
    db = Database(db_path)
    db.initialize()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO series (id, slug, revision, required_ratios,"
            " subtitle_languages, created_at, updated_at)"
            " VALUES ('s1', 'mig', 1, '[]', '[]', datetime('now'), datetime('now'))"
        )
        conn.execute(
            "INSERT INTO chapters (id, series_id, number, revision, created_at,"
            " updated_at) VALUES ('c1', 's1', 1, 1, datetime('now'), datetime('now'))"
        )
        conn.execute(
            "INSERT INTO pages (id, chapter_id, series_id, birth_number,"
            " sequence_position, source_path, created_at, updated_at)"
            " VALUES ('pg1', 'c1', 's1', 1, 1, 'p001.png', datetime('now'),"
            " datetime('now'))"
        )
        conn.execute(
            "INSERT INTO panels (id, page_id, series_id, chapter_id, birth_ordinal,"
            " bbox_x, bbox_y, bbox_w, bbox_h, crop_revision, reading_order,"
            " created_at, updated_at) VALUES ('p1', 'pg1', 's1', 'c1', 1, 0, 0,"
            " 100, 100, 1, 1, datetime('now'), datetime('now'))"
        )
    # reopen: migration to v2 must not destroy data
    db2 = Database(db_path)
    db2.initialize()
    assert db2.schema_version() >= 2
    with db2.transaction() as conn:
        n_series = conn.execute("SELECT COUNT(*) FROM series").fetchone()[0]
        n_panels = conn.execute("SELECT COUNT(*) FROM panels").fetchone()[0]
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert n_series == 1 and n_panels == 1
    assert {"assets", "story_beats", "shot_plan_revisions"} <= tables


def test_unknown_nonempty_store_still_refused(tmp_path: Path):
    import sqlite3

    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "koma.sqlite3").write_bytes(b"x" * 8192)  # not a database
    db = Database(tmp_path)
    with pytest.raises(sqlite3.DatabaseError):
        db.initialize()


def test_older_approval_does_not_reuse_revision_number(env):
    _, assets, _, sid = env
    asset = assets.create_asset(sid, "character", "revision-test")["asset_id"]
    assets.add_revision(asset, {})
    assets.add_revision(asset, {})
    assets.approve_revision(asset, 1, approved_by="owner")
    assert assets.add_revision(asset, {})["revision"] == 3


def test_unknown_coverage_reports_error_instead_of_crashing(env):
    _, _, shots, sid = env
    beat = shots.add_beat(sid, "ch_test", 1, beat_index=1, source_panel_ids=["p1"])
    shot = shots.add_shot_revision(
        sid, "ch_test", beat_coverage=[beat["beat_id"], "unknown"], source_panel_ids=["p1"]
    )
    result = shots.validate_coverage("ch_test", 1, [shot["shot_revision_id"]])
    assert not result["valid"]
    assert any("unknown beat" in issue for issue in result["problems"])


def test_duplicate_dialogue_across_different_beats_is_rejected(env):
    _, _, shots, sid = env
    revisions = []
    for i in (1, 2):
        beat = shots.add_beat(
            sid, "ch_test", 1, beat_index=i, source_panel_ids=["p1"], dialogue_line_ids=["ln:p1:0"]
        )
        revisions.append(
            shots.add_shot_revision(
                sid, "ch_test", beat_coverage=[beat["beat_id"]], source_panel_ids=["p1"]
            )["shot_revision_id"]
        )
    assert not shots.validate_coverage("ch_test", 1, revisions)["valid"]


def test_migrations_roll_back_ddl_and_version_together(tmp_path, monkeypatch):
    import sqlite3

    import koma.storage.database as module

    db = Database(tmp_path)
    db.path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db.path)
    conn.executescript(module._SCHEMA_V1)
    conn.execute("INSERT INTO schema_migrations VALUES (1, datetime('now'))")
    conn.commit()
    conn.close()
    original = module._MIGRATION_V3
    monkeypatch.setattr(module, "_MIGRATION_V3", original + "\nTHIS IS NOT SQL;")
    with pytest.raises(sqlite3.Error):
        db.initialize()
    assert db.schema_version() == 1
    assert (
        db.connection().execute("SELECT name FROM sqlite_master WHERE name='assets'").fetchone()
        is None
    )
    monkeypatch.setattr(module, "_MIGRATION_V3", original)
    db.initialize()
    assert db.schema_version() == module.SCHEMA_VERSION


def test_future_schema_is_rejected(env):
    db, *_ = env
    with db.transaction() as conn:
        conn.execute("INSERT INTO schema_migrations VALUES (999, datetime('now'))")
    with pytest.raises(RuntimeError, match="future schema"):
        db.initialize()
