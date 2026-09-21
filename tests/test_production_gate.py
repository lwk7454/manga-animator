"""Production route gate tests (G2/G3 — shared production gate)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from koma.application.production_gate import check_production_route
from koma.storage.errors import BlockedPrerequisite

sys.path.insert(0, str(Path(__file__).parent))
from test_r2v_worker import _create_work, _job
from test_r2v_worker import env as _env_fixture

env = _env_fixture


def test_direct_r2v_passes(env):
    store, _lock = env
    job = _job()
    _create_work(store, job)
    ready = check_production_route(store.db, "sr:1", job.ratio, "")
    assert ready["route"] == "direct_r2v"


def test_composition_assisted_requires_role(env):
    store, _lock = env
    job = _job()
    _create_work(store, job)
    with store.db.transaction() as conn:
        conn.execute(
            "UPDATE shot_plan_revisions SET route='composition_assisted'"
            " WHERE id='sr:1'",
        )
    with pytest.raises(BlockedPrerequisite):
        check_production_route(store.db, "sr:1", job.ratio, "")
    ready = check_production_route(
        store.db, "sr:1", job.ratio, "composition_i2v_first_frame"
    )
    assert ready["route"] == "composition_assisted"


