"""R4 — shot planner tests: proposal, grouping, ratio specs, invented events."""

from __future__ import annotations

from pathlib import Path

import pytest

from koma.planner import (
    DEFAULT_GRIDS,
    fingerprint_spec,
    propose_beats_from_script,
    propose_shots_from_beats,
    spec_ratios_for_shot,
    validate_no_invented_events,
)
from koma.storage.database import Database
from koma.storage.shot_store import ShotStore


@pytest.fixture()
def shots(tmp_path: Path) -> tuple[ShotStore, str, str]:
    db = Database(tmp_path)
    db.initialize()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO series (id, slug, revision, required_ratios,"
            " subtitle_languages, created_at, updated_at)"
            " VALUES ('ser1', 'demo', 1, '[\"16:9\",\"9:16\"]',"
            " '[\"en\"]', datetime('now'), datetime('now'))")
        conn.execute(
            "INSERT INTO chapters (id, series_id, number, revision, created_at,"
            " updated_at) VALUES ('ch1', 'ser1', 1, 1, datetime('now'), datetime('now'))")
    return ShotStore(db), "ser1", "ch1"


def test_beats_from_script_dialogue_only(shots):
    store, sid, ch = shots
    panels = [{"panel_id": f"p{i}"} for i in (1, 2, 3)]
    script = {
        "p1": {"dialogue": [{"jp": "こんにちは"}]},
        "p2": {"dialogue": [{"jp": "  "}]},  # blank → no line
        "p3": {},
    }
    beats = propose_beats_from_script(ch, sid, 1, panels, script, store)
    assert [b["beat_index"] for b in beats] == [1, 2, 3]
    # p1 has one valid dialogue line
    all_beats = store.beats_for_plan(ch, 1)
    assert all_beats[0]["dialogue_line_ids"] == ["ln:p1:0"]
    assert all_beats[1]["dialogue_line_ids"] == []
    assert all_beats[2]["dialogue_line_ids"] == []


def test_shots_dialogue_own_shot_non_dialogue_groups(shots):
    store, sid, ch = shots
    panels = [{"panel_id": f"p{i}"} for i in (1, 2, 3, 4)]
    script = {"p1": {}, "p2": {"dialogue": [{"jp": "あ"}]},
          "p3": {}, "p4": {"dialogue": [{"jp": "い"}]}}
    beats = propose_beats_from_script(ch, sid, 1, panels, script, store)
    proposed = propose_shots_from_beats(sid, ch, beats, store)
    # b1 (no dialogue) groups alone (max 1), b2 dialogue own shot, b3 alone, b4 own
    assert len(proposed) == 4
    # ordering preserved: beat indices ascend across proposals
    seq = []
    for p in proposed:
        rev = store.get_shot_revision(p["shot_revision_id"])
        for bid in rev["beat_coverage"]:
            seq.append(next(b["beat_index"] for b in beats if b["beat_id"] == bid))
    assert seq == sorted(seq)


def test_grouped_multi_panel_shot(shots):
    store, sid, ch = shots
    panels = [{"panel_id": f"p{i}"} for i in (1, 2, 3)]
    script = {"p1": {}, "p2": {}, "p3": {"dialogue": [{"jp": "x"}]}}
    beats = propose_beats_from_script(ch, sid, 1, panels, script, store)
    proposed = propose_shots_from_beats(sid, ch, beats, store, max_beats_per_shot=3)
    # p1, p2 (no dialogue) + p3 (dialogue) group into ONE shot covering 3 beats
    assert len(proposed) == 1
    rev = store.get_shot_revision(proposed[0]["shot_revision_id"])
    assert rev["source_panel_ids"] == ["p1", "p2", "p3"]
    ok = store.validate_coverage(ch, 1, [proposed[0]["shot_revision_id"]])
    assert ok["valid"], ok["problems"]


def test_coverage_after_planning(shots):
    store, sid, ch = shots
    panels = [{"panel_id": f"p{i}"} for i in (1, 2)]
    script = {"p1": {"dialogue": [{"jp": "a"}]}, "p2": {"dialogue": [{"jp": "b"}]}}
    beats = propose_beats_from_script(ch, sid, 1, panels, script, store)
    proposed = propose_shots_from_beats(sid, ch, beats, store)
    coverage = store.validate_coverage(
        ch, 1, [p["shot_revision_id"] for p in proposed])
    assert coverage["valid"]
    assert coverage["beats_covered"] == 2


def test_invented_events_rejected(shots):
    store, sid, ch = shots
    beats = propose_beats_from_script(ch, sid, 1,
                                      [{"panel_id": "p1"}], {"p1": {}}, store)
    from koma.planner import PlanProposalError
    with pytest.raises(PlanProposalError):
            store.beats_for_plan(ch, 1),
            [{"beat_coverage": ["beat:nonexistent",
                                 beats[0]["beat_id"]]}])


def test_ratio_specs_both_orientations(shots):
    store, sid, ch = shots
    b = store.add_beat(sid, ch, 1, beat_index=1, source_panel_ids=["p1"])
    s1 = store.add_shot_revision(sid, ch, beat_coverage=[b["beat_id"]],
                                 source_panel_ids=["p1"])
    for ratio, (w, h) in DEFAULT_GRIDS.items():
        spec = spec_ratios_for_shot(store, s1["shot_revision_id"], ratio,
                                    source_panel_size=(457, 452), profile="p")
        assert spec["ratio"] == ratio
        got = store.get_ratio_spec(s1["shot_revision_id"], ratio)
        assert (got["target_width"], got["target_height"]) == (w, h)
    # deterministic fingerprint: same inputs → same hash
    fp1 = fingerprint_spec(s1["shot_revision_id"], "16:9", 864, 480, {}, None)
    fp2 = fingerprint_spec(s1["shot_revision_id"], "16:9", 864, 480, {}, None)
    assert fp1 == fp2
    fp3 = fingerprint_spec(s1["shot_revision_id"], "16:9", 864, 480, {"scale": "x"}, None)
    assert fp3 != fp1  # placement change → different fingerprint


def test_duplicate_beat_index_rejected(shots):
    store, sid, ch = shots
    store.add_beat(sid, ch, 1, beat_index=1, source_panel_ids=["p1"])
    from koma.storage.errors import InvalidTransition
    with pytest.raises(InvalidTransition):
        store.add_beat(sid, ch, 1, beat_index=1, source_panel_ids=["p2"])
