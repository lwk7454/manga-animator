"""P08 — profile pins, verification vs qualification distinction (§15).

§23 profiles rows: bad pin / missing weights / wrong output socket fail
BEFORE upload with the actual effective profile; pin authority is the series
DB row; qualification is invalidated by any identity change; verification
alone can never satisfy the production gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from koma.adapters import qualification as qual
from koma.storage.database import Database
from koma.storage.domain_store import KomaStore
from koma.storage.errors import BlockedPrerequisite, RevisionConflict

CONFIG = Path(__file__).resolve().parents[1] / "config"


@pytest.fixture()
def store(tmp_path: Path) -> KomaStore:
    db = Database(tmp_path)
    db.initialize()
    s = KomaStore(db)
    s.create_series("demo")
    return s


# ── pin authority (§15.1) ────────────────────────────────────────────────────


def test_effective_profile_prefers_series_pin_over_default(store):
    series = store.require_series("demo")
    assert qual.effective_profile(CONFIG, series, "fallback") == "fallback"  # unpinned
    qual.set_pin(store, "demo", "manganinja_sd15")
    series2 = store.require_series("demo")
    assert qual.effective_profile(CONFIG, series2, "fallback") == "manganinja_sd15"


def test_pin_bumps_series_revision_and_cas_protects(store):
    res = qual.set_pin(store, "demo", "minimax_h3_fused_turbo")
    assert res["series_revision"] == 2
    # stale expected revision is refused
    with pytest.raises(RevisionConflict):
        store.set_series_profile_pin("demo", "other", 1)
    # unpinning works too
    res2 = qual.set_pin(store, "demo", None)
    assert res2["new_pin"] is None


def test_pin_reports_affected_semantics(store):
    res = qual.set_pin(store, "demo", "x")
    assert "historical" in res["note"] and "replan" in res["note"]


# ── verification is static and honest (§15.3) ────────────────────────────────


def test_verify_real_shipped_profile_passes():
    res = qual.verify_profile(CONFIG, "manganinja_sd15")
    assert res["verified"], res["errors"]
    assert res["workflow_hash"]
    slots = {c["name"] for c in res["checks"]}
    assert "inject_nodes_exist" in slots and "video_slots_complete" not in slots


def test_verify_missing_profile_fails_cleanly():
    res = qual.verify_profile(CONFIG, "no_such_profile")
    assert not res["verified"]
    assert any("profile_load" in e for e in res["errors"])


def test_verify_broken_inject_fails(tmp_path: Path):
    import shutil

    (tmp_path / "models" / "profiles").mkdir(parents=True)
    (tmp_path / "workflows").mkdir()
    shutil.copy(CONFIG / "workflows" / "manganinja_colorize.json", tmp_path / "workflows")
    bad = (CONFIG / "models" / "profiles" / "manganinja_sd15.toml").read_text()
    bad = bad.replace('lineart      = "13.inputs.image"', 'lineart      = "999.inputs.image"')
    (tmp_path / "models" / "profiles" / "manganinja_sd15.toml").write_text(bad)
    res = qual.verify_profile(tmp_path, "manganinja_sd15")
    assert not res["verified"]
    assert any("inject_nodes_exist" in e for e in res["errors"])


# ── qualification lifecycle ──────────────────────────────────────────────────


def test_unqualified_by_default_and_blocks_production(store):
    st = qual.qualification_status(store, "manganinja_sd15")
    assert st["status"] == "unqualified"
    assert "never probed" in st["reason"]
    with pytest.raises(BlockedPrerequisite, match="not qualified"):
        qual.require_qualified(store, "manganinja_sd15", "wf")


def test_probe_failure_records_honest_status(store):
    qual.record_qualification(
        store, profile_id="manganinja_sd15", workflow_id="wf",
        component_identity={"sk_model": "abc"}, server_fingerprint="srv1",
        captured_schema_hash="schema1", status="failed",
        reason="MangaNinja loader error: missing controlnet.pth",
    )
    st = qual.qualification_status(store, "manganinja_sd15")
    assert st["status"] == "failed"
    with pytest.raises(BlockedPrerequisite, match="failed"):
        qual.require_qualified(store, "manganinja_sd15", "wf")


def test_qualification_upsert_and_invalidations(store):
    ident = {"weights": "hash-a", "nodes": "v1"}
    qual.record_qualification(
        store, profile_id="p1", workflow_id="wf", component_identity=ident,
        server_fingerprint="srv1", captured_schema_hash="s1", status="qualified",
        measured_metadata={"wall_s": 45.2},
    )
    row = qual.require_qualified(store, "p1", "wf")
    assert row["measured_metadata"]["wall_s"] == 45.2
    # same identity re-check passes without re-probe
    qual.record_qualification(
        store, profile_id="p1", workflow_id="wf", component_identity=ident,
        server_fingerprint="srv1", captured_schema_hash="s1", status="qualified",
        measured_metadata={"wall_s": 44.9},
    )
    # ANY identity change invalidates: workflow bytes changed → new record
    # for a different workflow id doesn't satisfy the old one
    with pytest.raises(BlockedPrerequisite):
        qual.require_qualified(store, "p1", "wf-v2")
    # component bytes changed → the honest path is a failed/unqualified record
    qual.record_qualification(
        store, profile_id="p1", workflow_id="wf",
        component_identity={"weights": "hash-B", "nodes": "v1"},
        server_fingerprint="srv1", captured_schema_hash="s1", status="unqualified",
        reason="component weights changed; re-probe required",
    )
    st = qual.qualification_status(store, "p1")
    assert st["status"] == "unqualified"
    with pytest.raises(BlockedPrerequisite):
        qual.require_qualified(store, "p1", "wf")


def test_identity_hash_changes_with_any_component(store):
    base: dict = dict(profile_id="p", workflow_id="w", component_identity={"a": 1},
                      server_fingerprint="s", captured_schema_hash="c", status="qualified")
    h1 = qual.record_qualification(store, **base)["identity_hash"]
    h2 = qual.record_qualification(
        store, profile_id="p", workflow_id="w", component_identity={"a": 1},
        server_fingerprint="s2", captured_schema_hash="c", status="qualified",
    )["identity_hash"]
    h3 = qual.record_qualification(
        store, profile_id="p", workflow_id="w", component_identity={"a": 1},
        server_fingerprint="s", captured_schema_hash="c2", status="qualified",
    )["identity_hash"]
    h4 = qual.record_qualification(
        store, profile_id="p", workflow_id="w", component_identity={"a": 2},
        server_fingerprint="s", captured_schema_hash="c", status="qualified",
    )["identity_hash"]
    assert len({h1, h2, h3, h4}) == 4


def test_doctor_style_summary_distinguishes_three_claims(store):
    """Existence, verification and qualification are different claims (§15.3)."""
    verified = qual.verify_profile(CONFIG, "manganinja_sd15")
    st = qual.qualification_status(store, "manganinja_sd15")
    assert verified["verified"] is True  # files/structure OK
    assert st["status"] == "unqualified"  # never probed on hardware
    # both true at once = the honest "installed but unqualified" state
