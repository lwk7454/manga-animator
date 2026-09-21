from koma.application.production_gate import check_production_route
from koma.storage.errors import BlockedPrerequisite
from tests.test_r2v_worker import _create_work, _job, env  # noqa: F401


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
        conn.execute("UPDATE shot_plan_revisions SET route='composition_assisted'")
    try:
        check_production_route(store.db, "sr:1", job.ratio, "")
        raise AssertionError("expected BlockedPrerequisite")
    except BlockedPrerequisite as exc:
        assert "adapter role" in str(exc)
    ready = check_production_route(
        store.db, "sr:1", job.ratio, "composition_i2v_first_frame"
    )
    assert ready["route"] == "composition_assisted"
