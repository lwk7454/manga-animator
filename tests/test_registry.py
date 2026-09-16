"""P07 — registry lifecycle, sheet approval chain, bindings (§14).

§23 references rows: rejected/unapproved revisions cannot enter production;
exact approved variants reach the colorize request; revised sheets
invalidate only descendants; proposals never auto-approve.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from koma.steps import registry as reg
from koma.storage.database import Database
from koma.storage.domain_store import KomaStore
from koma.storage.errors import BlockedPrerequisite, InvalidTransition, NotFound


@pytest.fixture()
def store(tmp_path: Path) -> KomaStore:
    db = Database(tmp_path)
    db.initialize()
    s = KomaStore(db)
    s.create_series("demo")
    s.ensure_chapter("demo", 1)
    s.import_pages(
        "demo", 1,
        [{"source_path": "001.jpg", "sha256": "a" * 64,
          "panels": [{"panel_id": "ch01_p001_01",
                      "bbox": {"x": 10, "y": 10, "w": 700, "h": 600}}]}],
        operation_id="op-1", import_source_id="src-1",
    )
    return s


def _char(store: KomaStore, entity_id="heroine", payload=None) -> int:
    res = store.save_registry_proposal(entity_id, "character",
                                       payload or {"name": "heroine"})
    return res["revision"]


def test_candidate_pairing_rejects_foreign_selection(store):
    cands = [
        {"panel_id": "ch01_p001_01", "artifact_relpath": "a/1", "score": 0.9},
        {"panel_id": "ch01_p002_01", "artifact_relpath": "a/2", "score": 0.4},
    ]
    res = reg.save_canonical_candidates(store, "heroine", cands, cands[0])
    assert res["status"] == "proposed"
    with pytest.raises(ValueError, match="candidate set"):
        reg.save_canonical_candidates(store, "heroine", cands,
                                      {"panel_id": "ghost", "artifact_relpath": "x"})


def test_proposals_never_autoapprove(store):
    _char(store, "heroine")
    latest = store.latest_registry("heroine")
    assert latest["status"] == "proposed"


def test_rejected_revision_cannot_be_approved_or_consumed(store):
    rev = _char(store, "villain")
    reg.reject_card(store, "villain", rev)
    with pytest.raises(InvalidTransition):
        store.approve_registry_revision("villain", rev)
    with pytest.raises((BlockedPrerequisite, NotFound)):
        reg.require_approved_character(store, "villain")


def test_draft_sheet_requires_approved_source(store):
    rev = _char(store, "heroine")
    with pytest.raises(BlockedPrerequisite, match="not approved"):
        reg.request_draft_sheet(store, "heroine", rev, {"hair": "#332211"})
    reg.approve_card(store, "heroine", rev)
    draft = reg.request_draft_sheet(store, "heroine", rev, {"hair": "#332211"})
    assert draft["status"] == "proposed"
    # the draft itself is NOT consumable until its own approval
    with pytest.raises(BlockedPrerequisite):
        reg.require_approved_variant(store, draft["entity_id"], draft["revision"])


def test_full_sheet_chain_reaches_colorize_reference(store):
    rev = _char(store, "heroine")
    reg.approve_card(store, "heroine", rev)
    draft = reg.request_draft_sheet(store, "heroine", rev, {"hair": "#332211"})
    reg.approve_sheet(store, draft["entity_id"], draft["revision"], "artifact-1")
    # bind panel to the approved character + variant
    store.set_panel_bindings("ch01_p001_01", None, [{
        "character_id": "heroine", "character_revision": rev,
        "variant_id": draft["entity_id"], "variant_revision": draft["revision"],
    }])
    ref = reg.compile_colorize_reference(store, "ch01_p001_01", "heroine")
    assert ref["sheet_artifact_id"] == "artifact-1"
    assert ref["character_revision"] == rev


def test_unapproved_variant_blocked_in_production(store):
    rev = _char(store, "heroine")
    reg.approve_card(store, "heroine", rev)
    draft = reg.request_draft_sheet(store, "heroine", rev, {})
    # bind with a PROPOSED variant: production must refuse
    with pytest.raises(BlockedPrerequisite, match="not approved"):
        store.set_panel_bindings("ch01_p001_01", None, [{
            "character_id": "heroine", "character_revision": rev,
            "variant_id": draft["entity_id"],
            "variant_revision": draft["revision"],
        }])


def test_missing_voice_is_not_silently_replaced(store):
    rev = _char(store, "heroine")
    reg.approve_card(store, "heroine", rev)
    # no voice revision exists for the character: setup blocker, not a fallback
    latest_voice = store.latest_registry("heroine:voice")
    assert latest_voice is None  # absent: setup blocker, not fallback
    with pytest.raises(NotFound):
        reg.require_approved_character(store, "heroine:voice")


def test_revised_sheet_invalidates_only_descendants(store):
    rev = _char(store, "heroine")
    reg.approve_card(store, "heroine", rev)
    draft = reg.request_draft_sheet(store, "heroine", rev, {})
    reg.approve_sheet(store, draft["entity_id"], draft["revision"], "artifact-1")
    store.set_panel_bindings("ch01_p001_01", None, [{
        "character_id": "heroine", "character_revision": rev,
        "variant_id": draft["entity_id"], "variant_revision": draft["revision"],
    }])
    # plan colorize work consuming the OLD revision
    sid = store.get_series_by_slug("demo")["id"]
    w = store.plan_work([{
        "scope_key": "panel:ch01_p001_01", "stage": "colorize", "ratio": "",
        "mode": "production", "input_fingerprint": "fp-old", "desired_spec": {
            "character_id": "heroine", "character_revision": rev},
        "series_id": sid,
    }])[0]
    # the character gets a NEW approved revision
    r2 = store.save_registry_proposal("heroine", "character", {"name": "heroine v2"})
    reg.approve_card(store, "heroine", r2["revision"])
    stale = reg.revised_sheet_invalidates_descendants(store, "heroine")
    assert stale == [w["work_id"]]
    assert store.get_work(w["work_id"])["state"] == "superseded"


def test_binding_revision_increments(store):
    rev = _char(store, "heroine")
    reg.approve_card(store, "heroine", rev)
    b1 = store.set_panel_bindings("ch01_p001_01", None, [
        {"character_id": "heroine", "character_revision": rev}])
    b2 = store.set_panel_bindings("ch01_p001_01", None, [
        {"character_id": "heroine", "character_revision": rev}])
    assert b2["binding_revision"] == b1["binding_revision"] + 1


def test_background_panel_explicitly_unbound(store):
    """An explicit empty binding differs from missing analysis (§14.1)."""
    store.import_pages(
        "demo", 1,
        [{"source_path": "002.jpg", "sha256": "b" * 64,
          "panels": [{"panel_id": "ch01_bg",
                      "bbox": {"x": 0, "y": 0, "w": 100, "h": 100}}]}],
        operation_id="op-2", import_source_id="src-1",
    )
    b = store.set_panel_bindings("ch01_bg", None, [])
    assert b["binding_revision"] == 1
    assert store.latest_panel_bindings("ch01_bg") is not None
