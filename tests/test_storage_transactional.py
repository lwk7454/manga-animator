"""P01 — transactional metadata, CAS, identities and attempt lifecycle (§5, §9).

Durable behavior tests: two-connection conflicts, idempotent operations,
immutable identities, attempt transitions, uncertainty holds, stale-result
fencing and recovery adoption. All offline; no model/remote calls.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from koma.models.work import AttemptState, WorkState
from koma.storage.database import Database
from koma.storage.domain_store import KomaStore
from koma.storage.errors import (
    BlockedPrerequisite,
    DuplicateOperation,
    InvalidTransition,
    NotFound,
    RevisionConflict,
)


@pytest.fixture()
def store(tmp_path: Path) -> KomaStore:
    db = Database(tmp_path)
    db.initialize()
    return KomaStore(db)


@pytest.fixture()
def chapter(store: KomaStore) -> tuple[str, str]:
    """Series 'demo' + chapter 1 with one imported page of two panels."""
    store.create_series("demo")
    store.ensure_chapter("demo", 1)
    r = store.import_pages(
        "demo",
        1,
        [
            {
                "source_path": "001.jpg",
                "sha256": "a" * 64,
                "width": 1654,
                "height": 2339,
                "direction": "rtl",
                "panels": [
                    {"panel_id": "ch01_p001_01", "bbox": {"x": 10, "y": 10, "w": 700, "h": 600}},
                    {"panel_id": "ch01_p001_02", "bbox": {"x": 850, "y": 10, "w": 700, "h": 600}},
                ],
            }
        ],
        operation_id="op-1",
        import_source_id="src-1",
    )
    return r["page_ids"][0], store.get_series_by_slug("demo")["id"]


def _colorize_spec(store: KomaStore, panel_id: str = "ch01_p001_01", fp: str = "fp1"):
    return {
        "scope_key": f"panel:{panel_id}",
        "stage": "colorize",
        "ratio": "",
        "mode": "production",
        "input_fingerprint": fp,
        "desired_spec": {"long_side": 512},
        "series_id": store.get_series_by_slug("demo")["id"],
        "chapter_id": store.get_chapter("demo", 1)["id"],
    }


# ── schema / init ────────────────────────────────────────────────────────────


def test_reopen_preserves_committed_state(tmp_path: Path):
    db = Database(tmp_path)
    db.initialize()
    store = KomaStore(db)
    store.create_series("keep")
    db2 = Database(tmp_path)
    db2.initialize()
    assert KomaStore(db2).get_series_by_slug("keep") is not None


def test_refuses_unrecognized_nonempty_store(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "koma.sqlite3").write_bytes(b"x" * 8192)  # nonempty, not a database
    db = Database(tmp_path)
    with pytest.raises(sqlite3.DatabaseError):
        db.initialize()
    # preserved, not deleted
    assert (state / "koma.sqlite3").exists()


def test_refuses_nonempty_unrecognized_sqlite(tmp_path: Path):
    """A valid sqlite file without our schema is unknown data: refuse (D01)."""
    p = tmp_path / "state" / "koma.sqlite3"
    p.parent.mkdir(parents=True)
    c = _alien_connect(p)
    c.execute("CREATE TABLE alien (x)")
    c.commit()
    c.close()
    db = Database(tmp_path)
    with pytest.raises(RuntimeError, match="unrecognized nonempty store"):
        db.initialize()
    assert p.exists()


def _alien_connect(p: Path):
    import sqlite3

    return sqlite3.connect(p)


def test_schema_version_recorded(tmp_path: Path):
    db = Database(tmp_path)
    db.initialize()
    assert db.schema_version() >= 1


# ── identities / CAS ─────────────────────────────────────────────────────────


def test_two_writers_one_revision_winner(store: KomaStore):
    store.create_series("casdemo")
    ok: dict[str, int] = {}

    def worker(name: str, exp: int):
        s = KomaStore(Database(store.db.data_dir))
        try:
            s.set_series_profile_pin("casdemo", "p1", exp)
            ok[name] = 1
        except RevisionConflict:
            ok[name] = 0

    t1 = threading.Thread(target=worker, args=("a", 1))
    t2 = threading.Thread(target=worker, args=("b", 1))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert sum(ok.values()) == 1


def test_series_slug_unique(store: KomaStore):
    store.create_series("dup")
    with pytest.raises(DuplicateOperation):
        store.create_series("dup")


def test_panel_ids_immutable_through_reorder(store: KomaStore, chapter):
    page_id, _sid = chapter
    before = [p["id"] for p in store.load_panels("demo", 1)]
    store.set_page_order(page_id, 0, list(reversed(before)), "ord-1")
    after = [p["id"] for p in store.load_panels("demo", 1)]
    assert sorted(before) == sorted(after)  # identity preserved; only order moved


def test_order_requires_full_permutation(store: KomaStore, chapter):
    page_id, _ = chapter
    with pytest.raises(ValueError, match="every active panel"):
        store.set_page_order(page_id, 0, ["ch01_p001_01"], "ord-x")


def test_stale_order_write_rejected(store: KomaStore, chapter):
    page_id, _ = chapter
    store.set_page_order(page_id, 0, ["ch01_p001_02", "ch01_p001_01"], "ord-1")
    with pytest.raises(RevisionConflict):
        store.set_page_order(page_id, 0, ["ch01_p001_01", "ch01_p001_02"], "ord-2")


def test_repeated_order_operation_idempotent(store: KomaStore, chapter):
    page_id, _ = chapter
    first = store.set_page_order(page_id, 0, ["ch01_p001_02", "ch01_p001_01"], "ord-1")
    again = store.set_page_order(page_id, 0, ["ch01_p001_02", "ch01_p001_01"], "ord-1")
    assert again["idempotent"] is True
    assert again["order_review_revision"] == first["order_review_revision"]


def test_import_reimport_noop_append_and_conflict(store: KomaStore, chapter):
    _page_id, _ = chapter
    before = {p["id"] for p in store.load_pages("demo", 1)}
    # unchanged reimport: no-op
    r = store.import_pages(
        "demo", 1,
        [{"source_path": "001.jpg", "sha256": "a" * 64, "panels": []}],
        operation_id="op-2", import_source_id="src-1",
    )
    assert r["created"] == [] or r["page_ids"] == []
    assert {p["id"] for p in store.load_pages("demo", 1)} == before
    # new path appends
    r2 = store.import_pages(
        "demo", 1,
        [{"source_path": "002.jpg", "sha256": "b" * 64, "panels": []}],
        operation_id="op-3", import_source_id="src-1",
    )
    assert len(r2["created"]) == 1
    # same path changed bytes: explicit replacement blocker
    with pytest.raises(DuplicateOperation, match="replace"):
        store.import_pages(
            "demo", 1,
            [{"source_path": "002.jpg", "sha256": "c" * 64, "panels": []}],
            operation_id="op-4", import_source_id="src-1",
        )


def test_chapter_scope_isolated(store: KomaStore, chapter):
    """Chapter 100 with same local page numbering stays distinct (B24)."""
    store.ensure_chapter("demo", 100)
    store.import_pages(
        "demo", 100,
        [{"source_path": "001.jpg", "sha256": "z" * 64,
          "panels": [{"panel_id": "ch100_p001_01", "bbox": {"x": 0, "y": 0, "w": 10, "h": 10}}]}],
        operation_id="op-100", import_source_id="src-100",
    )
    ch1 = store.load_panels("demo", 1)
    ch100 = store.load_panels("demo", 100)
    assert "ch01_p001_01" in [p["id"] for p in ch1]
    assert "ch100_p001_01" in [p["id"] for p in ch100]
    assert len(ch100) == 1


def test_invalid_ratio_rejected(store: KomaStore):
    with pytest.raises(ValueError, match="invalid ratio"):
        store.create_series("bad", required_ratios=["4:5"])


# ── work / attempts (§9) ─────────────────────────────────────────────────────


def test_plan_work_deduplicates_exact_specs(store: KomaStore, chapter):
    w1 = store.plan_work([_colorize_spec(store)])[0]
    w2 = store.plan_work([_colorize_spec(store)])[0]
    assert w1["work_id"] == w2["work_id"] and w2["created"] is False


def test_new_fingerprint_is_new_work(store: KomaStore, chapter):
    w1 = store.plan_work([_colorize_spec(store, fp="fp1")])[0]
    w2 = store.plan_work([_colorize_spec(store, fp="fp2")])[0]
    assert w1["work_id"] != w2["work_id"]


def test_claim_allocates_one_attempt_and_blocks_second(store: KomaStore, chapter):
    w = store.plan_work([_colorize_spec(store)])[0]
    a1 = store.claim_attempt(w["work_id"], "epoch-1")
    assert a1["attempt_number"] == 1
    with pytest.raises(InvalidTransition):
        store.claim_attempt(w["work_id"], "epoch-1")


def test_submission_uncertainty_holds_work(store: KomaStore, chapter):
    w = store.plan_work([_colorize_spec(store)])[0]
    aid = store.claim_attempt(w["work_id"], "epoch-1")["attempt_id"]
    store.begin_submission(aid, "epoch-1")
    store.mark_submission_uncertain(aid, "epoch-1", "timeout after dispatch")
    # no blind resubmission (D06)
    with pytest.raises(BlockedPrerequisite, match="submission_uncertain"):
        store.claim_attempt(w["work_id"], "epoch-1")
    # reconciliation resolves it
    store.record_remote_acceptance(aid, "epoch-1", "prompt-9", "comfy")
    assert store.get_attempt(aid)["state"] == "running"
    assert store.get_work(w["work_id"])["state"] == "active"
    store.record_attempt_outcome(aid, "epoch-1", AttemptState.DOWNLOADING)
    store.record_attempt_outcome(aid, "epoch-1", AttemptState.VALIDATING)
    store.promote_artifacts(aid, "epoch-1", "fp1", [
        {"kind": "color_master", "relative_path": "artifacts/x/payload.png",
         "sha256": "f" * 64, "size": 10, "output_role": "color_master"},
    ])
    assert store.get_work(w["work_id"])["state"] == "succeeded"


def test_attempt_numbers_increase_after_failure(store: KomaStore, chapter):
    w = store.plan_work([_colorize_spec(store)])[0]
    a1 = store.claim_attempt(w["work_id"], "epoch-1")
    store.record_attempt_outcome(a1["attempt_id"], "epoch-1", AttemptState.FAILED,
                                 error_kind="remote_failed", error_detail="boom")
    assert store.get_work(w["work_id"])["state"] == "failed"
    # bounded retry: explicit decision moves work back to ready
    store.set_work_state(w["work_id"], WorkState.READY)
    a2 = store.claim_attempt(w["work_id"], "epoch-1")
    assert a1["attempt_number"] == 1
    assert a2["attempt_number"] == 2


def test_attempt_transitions_enforced(store: KomaStore, chapter):
    w = store.plan_work([_colorize_spec(store)])[0]
    aid = store.claim_attempt(w["work_id"], "epoch-1")["attempt_id"]
    with pytest.raises(InvalidTransition):
        store.record_attempt_outcome(aid, "epoch-1", AttemptState.SUCCEEDED)
    store.record_attempt_outcome(aid, "epoch-1", AttemptState.FAILED)
    with pytest.raises(InvalidTransition):
        # terminal attempts never reset to prepared
        store.record_attempt_outcome(aid, "epoch-1", AttemptState.PREPARED)


def test_stale_result_cannot_promote(store: KomaStore, chapter):
    w2 = store.plan_work([_colorize_spec(store, fp="fp2")])[0]
    aid = store.claim_attempt(w2["work_id"], "epoch-1")["attempt_id"]
    store.begin_submission(aid, "epoch-1")
    store.record_remote_acceptance(aid, "epoch-1", "p", "srv")
    with pytest.raises(InvalidTransition, match="historical"):
        store.promote_artifacts(aid, "epoch-1", "OLD", [
            {"kind": "color_master", "relative_path": "a/b", "sha256": "e" * 64, "size": 1},
        ])


def test_promotion_swaps_current_output(store: KomaStore, chapter):
    w = store.plan_work([_colorize_spec(store)])[0]
    aid = store.claim_attempt(w["work_id"], "epoch-1")["attempt_id"]
    store.record_attempt_outcome(aid, "epoch-1", AttemptState.FAILED)  # fail first attempt
    store.set_work_state(w["work_id"], WorkState.READY)
    a2 = store.claim_attempt(w["work_id"], "epoch-1")
    assert a2["attempt_number"] == 2
    store.record_attempt_outcome(a2["attempt_id"], "epoch-1", AttemptState.SUBMITTING)
    store.record_remote_acceptance(a2["attempt_id"], "epoch-1", "pp", "srv")
    store.record_attempt_outcome(a2["attempt_id"], "epoch-1", AttemptState.DOWNLOADING)
    store.record_attempt_outcome(a2["attempt_id"], "epoch-1", AttemptState.VALIDATING)
    store.promote_artifacts(a2["attempt_id"], "epoch-1", "fp1", [
        {"kind": "color_master", "relative_path": "a/c", "sha256": "d" * 64, "size": 3},
    ])
    co = store.current_output("panel:ch01_p001_01", "colorize")
    assert co is not None


def test_epoch_fencing_after_adoption(store: KomaStore, chapter):
    w = store.plan_work([_colorize_spec(store)])[0]
    aid = store.claim_attempt(w["work_id"], "epoch-A")["attempt_id"]
    store.begin_submission(aid, "epoch-A")
    adopted = store.adopt_nonterminal_attempts("epoch-A", "epoch-B")
    assert adopted == [aid]
    with pytest.raises(RevisionConflict):
        store.record_attempt_outcome(aid, "epoch-A", AttemptState.DOWNLOADING)
    store.record_attempt_outcome(aid, "epoch-B", AttemptState.DOWNLOADING)


def test_intent_persisted_before_submission_side_effect(store: KomaStore, chapter):
    w = store.plan_work([_colorize_spec(store)])[0]
    aid = store.claim_attempt(w["work_id"], "epoch-1")["attempt_id"]
    store.record_attempt_intent(
        aid, "epoch-1", request_identity='{"n":1}', seed=7,
        upload_plan=["u.png"], output_prefix="koma_x",
    )
    row = store.get_attempt(aid)
    assert row["seed"] == 7 and row["output_prefix"] == "koma_x"
    # epoch mismatch fenced
    with pytest.raises(RevisionConflict):
        store.record_attempt_intent(aid, "epoch-2", request_identity="x", seed=1,
                                    upload_plan=[], output_prefix="y")


# ── ownership + control ──────────────────────────────────────────────────────


def test_control_request_idempotent_and_lifecycle(store: KomaStore):
    store.write_command_owner({"owner_uuid": "u1", "pid": 1, "epoch": "e1"})
    r1 = store.request_control("u1", "e1", "pause", "ctl-1")
    r2 = store.request_control("u1", "e1", "pause", "ctl-1")
    assert r1["id"] == r2["id"] == "ctl-1"
    store.acknowledge_control("ctl-1", "u1", "e1")
    store.complete_control("ctl-1", {"drained": True})
    assert store.pending_control_requests("u1", "e1") == []


def test_control_acknowledges_only_target_owner(store: KomaStore):
    store.write_command_owner({"owner_uuid": "u1", "pid": 1, "epoch": "e1"})
    store.request_control("u1", "e1", "pause", "ctl-2")
    with pytest.raises(RevisionConflict):
        store.acknowledge_control("ctl-2", "u1", "e9")


# ── runs ─────────────────────────────────────────────────────────────────────


def test_run_config_immutable_but_state_updatable(store: KomaStore):
    store.create_series("rundemo")
    run = store.create_run("rundemo", 1, ["16:9"], "production", {"fps": 24})
    store.update_run(run["run_id"], status="running")
    with pytest.raises(ValueError, match="immutable"):
        store.update_run(run["run_id"], config_hash="changed")


def test_derive_progress_counts_states(store: KomaStore, chapter):
    store.plan_work([
        _colorize_spec(store, fp="fp1"),
        _colorize_spec(store, fp="fp2"),
    ])
    progress = store.derive_progress("demo", 1)
    assert "colorize" in progress["work_by_stage"]
    states = progress["work_by_stage"]["colorize"]["shared"]
    assert sum(states.values()) == 2  # two distinct fingerprints → two work items


def test_missing_things_raise_not_found(store: KomaStore, chapter):
    with pytest.raises(NotFound):
        store.get_panel("nope")
    with pytest.raises(NotFound):
        store.get_work("nope")
    with pytest.raises(NotFound):
        store.get_attempt("nope")
    with pytest.raises(NotFound):
        store.require_series("ghost")
