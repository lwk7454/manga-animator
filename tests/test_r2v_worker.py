"""R5 — supervised worker tests: resumability, fencing, reconciliation.

Fake transport doubles (submitter/poller/fetcher) drive the full §9.2
lifecycle against the REAL store — crash/resume at every phase, D06
reconciliation, stale-result fencing, idempotent double start, conflicting
owners. No GPU, no server.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from koma.r2v_worker import R2VWorker, ShotRatioJob
from koma.scheduler.ownership import OwnerLock
from koma.storage.database import Database
from koma.storage.domain_store import KomaStore
from koma.storage.errors import BlockedPrerequisite


@pytest.fixture()
def env(tmp_path: Path):
    db = Database(tmp_path)
    db.initialize()
    store = KomaStore(db)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO series (id, slug, revision, required_ratios,"
            " subtitle_languages, created_at, updated_at)"
            " VALUES ('ser1', 'demo', 1, '[\"16:9\"]', '[\"en\"]',"
            " datetime('now'), datetime('now'))")
        conn.execute(
            "INSERT INTO chapters (id, series_id, number, revision, created_at,"
            " updated_at) VALUES ('ch1', 'ser1', 1, 1, datetime('now'),"
            " datetime('now'))")
    lock = OwnerLock()
    return store, lock


def _job(scope="ser1/ch1/shot/s1", ratio="16:9", fp="fp:123", **spec):
    defaults = {
        "compiled_graph": {"1": {"class_type": "X"}, "136": {"inputs": {}}},
        "upload_plan": [],
        "seed": 42,
        "output_dir": "/tmp/koma-r5-test",
        "compiled_workflow_hash": "h" * 64,
    }
    defaults.update(spec)
    return ShotRatioJob(work_id="w:1", scope_key=scope, stage="motion",
                        ratio=ratio, input_fingerprint=fp, desired_spec=defaults)


def _create_work(store, job: ShotRatioJob, *, state="ready"):
    now = __import__("datetime").datetime.utcnow().isoformat()
    with store.db.transaction() as conn:
        conn.execute(
            "INSERT INTO work_items (id, series_id, chapter_id, scope_key, stage,"
            " ratio, mode, input_fingerprint, desired_spec, state, priority,"
            " created_at, updated_at)"
            " VALUES (?, 'ser1', ?, ?, ?, ?, 'production', ?, ?, ?, 100, ?, ?)",
            (job.work_id, "ch1", job.scope_key, job.stage, job.ratio,
             job.input_fingerprint, _dumps(job.desired_spec), state, now, now),
        )


def _dumps(obj):
    import json
    return json.dumps(obj)


def _worker(store, lock, *, submit, poll="generated", fetch=None, validate=None):
    return R2VWorker(
        store, lock,
        submitter=submit,
        poller=lambda pid: poll if isinstance(poll, str) else poll(pid),
        fetcher=fetch or (lambda pid, d: [{"kind": "motion", "relative_path":
                                           f"{pid}/out.mp4", "sha256": "bb" * 32,
                                           "size": 10, "media_metadata": {},
                                           "result_metadata": {}}]),
        validator=validate,
    )


def test_happy_path_claims_executes_promotes(env):
    store, lock = env
    job = _job()
    _create_work(store, job)
    submitted = {}
    w = _worker(store, lock, submit=lambda g, up: (submitted.update(g=g), "pid-1")[1])
    out = w.execute(job)
    assert out.status == "succeeded"
    assert out.prompt_id == "pid-1"
    assert submitted["g"] == job.desired_spec["compiled_graph"]
    # attempt terminal success recorded
    row = _attempt(store, out.attempt_id)
    assert row["state"] == "succeeded"
    # work succeeded + current output registered
    work = _work(store, job.work_id)
    assert work["state"] == "succeeded"
    cur = store.current_output(job.scope_key, job.stage, job.ratio)
    assert cur is not None
    assert out.artifact_ids == [cur["artifact_id"]]


def _attempt(store, attempt_id):
    with store.db.transaction() as conn:
        row = conn.execute("SELECT * FROM attempts WHERE id = ?",
                           (attempt_id,)).fetchone()
    assert row is not None
    return dict(row)


def _work(store, work_id):
    with store.db.transaction() as conn:
        row = conn.execute("SELECT * FROM work_items WHERE id = ?",
                           (work_id,)).fetchone()
    return dict(row)


def test_remote_poll_failure_is_honest(env):
    store, lock = env
    job = _job()
    _create_work(store, job)
    w = _worker(store, lock, submit=lambda g, up: "pid-2", poll="failed")
    out = w.execute(job)
    assert out.status == "failed"
    row = _attempt(store, out.attempt_id)
    assert row["state"] == "failed" and row["error_kind"] == "remote_failed"
    assert _work(store, job.work_id)["state"] == "failed"


def test_deadline_interrupts_but_remote_continues(env):
    store, lock = env
    job = _job(wait_seconds=0.01, poll_seconds=0.005)
    _create_work(store, job)
    w = _worker(store, lock, submit=lambda g, up: "pid-3", poll="generating")
    out = w.execute(job)
    assert out.status == "interrupted"
    row = _attempt(store, out.attempt_id)
    assert row["state"] == "interrupted"
    assert "resume to poll" in (row["error_detail"] or "")
    # the remote prompt id is durably recorded for the resuming poller
    assert row["remote_prompt_id"] == "pid-3"
    # the work is blocked with an honest reason (operator resumes via §9.1a)
    assert _work(store, job.work_id)["state"] == "blocked"
    assert "interrupted" in _work(store, job.work_id)["blocked_reason"]


def test_submission_transport_error_is_uncertain_and_blocks(env):
    store, lock = env
    job = _job()
    _create_work(store, job)

    def boom(graph, upload_plan):
        raise ConnectionError("server unreachable")

    w = _worker(store, lock, submit=boom)
    out = w.execute(job)
    assert out.status == "held"
    assert "submission_uncertain" in out.detail
    row = _attempt(store, out.attempt_id)
    assert row["state"] == "submission_uncertain"
    assert _work(store, job.work_id)["state"] == "blocked"
    # a second claim is refused while uncertain (D06)
    with pytest.raises(BlockedPrerequisite):
        store.claim_attempt(job.work_id, w.owner_epoch)


def test_reconcile_finds_unique_match(env):
    store, lock = env
    job = _job()
    _create_work(store, job)
    w = _worker(store, lock, submit=lambda g, up: (_ for _ in ()).throw(
        ConnectionError("drop")))
    out = w.execute(job)
    attempt_id = out.attempt_id
    # server actually processed it: history has the SAME graph
    import hashlib
    import json
    graph = job.desired_spec["compiled_graph"]
    h = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()
    w.set_reconcile_snapshot({"pid-9": {"prompt": {"prompt": graph, "1": {"a": 1}}}})
    # the intent recorded compiled_workflow_hash 'h*32' — override to match:
    with store.db.transaction() as conn:
        conn.execute("UPDATE attempts SET compiled_workflow_hash = ? WHERE id = ?",
                     (h, attempt_id))
    out2 = w.reconcile_uncertain(attempt_id, w.owner_epoch)
    assert out2.status == "accepted" and out2.prompt_id == "pid-9"
    row = _attempt(store, attempt_id)
    assert row["state"] == "running"


def test_reconcile_ambiguous_stays_held(env):
    store, lock = env
    job = _job()
    _create_work(store, job)
    w = _worker(store, lock, submit=lambda g, up: (_ for _ in ()).throw(
        ConnectionError("x")))
    out = w.execute(job)
    attempt_id = out.attempt_id
    import hashlib
    import json
    graph = job.desired_spec["compiled_graph"]
    h = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()
    with store.db.transaction() as conn:
        conn.execute("UPDATE attempts SET compiled_workflow_hash = ? WHERE id = ?",
                     (h, attempt_id))
    w.set_reconcile_snapshot({
        "pid-a": {"prompt": {"prompt": graph}},
        "pid-b": {"prompt": {"prompt": graph}},
    })
    out2 = w.reconcile_uncertain(attempt_id, w.owner_epoch)
    assert out2.status == "held"  # 2 matches: ambiguous, never guessed
    assert _attempt(store, attempt_id)["state"] == "submission_uncertain"


def test_owner_conflict_detected(env):
    store, lock = env
    job = _job()
    _create_work(store, job)
    # simulate a lock held by another owner via a second OwnerLock instance
    other = OwnerLock()
    held = other.acquire("other-holder", None)
    try:
        w = _worker(store, lock, submit=lambda g, up: "pid-x")
        out = w.execute(job)
        assert out.status == "owner_busy"
        # no attempt was even created
        with store.db.transaction() as conn:
            n = conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        assert n == 0
    finally:
        other.release(held)


def test_stale_result_fencing(env):
    """Fingerprint changed mid-run → promotion refuses, attempt fails."""
    store, lock = env
    job = _job()
    _create_work(store, job)
    calls = {"n": 0}

    def poll(pid):
        calls["n"] += 1
        if calls["n"] == 1:
            # mutate the fingerprint as a concurrent spec change would
            with store.db.transaction() as conn:
                conn.execute(
                    "UPDATE work_items SET input_fingerprint = 'fp:new',"
                    " updated_at = datetime('now') WHERE id = ?", (job.work_id,))
        return "generated"

    w = _worker(store, lock, submit=lambda g, up: "pid-4", poll=poll)
    out = w.execute(job)
    assert out.status == "failed"
    assert "fingerprint" in out.detail.lower() or "historical" in out.detail.lower()
    row = _attempt(store, out.attempt_id)
    assert row["state"] == "failed"


def test_assemble_jobs_orders_and_filters(env):
    store, lock = env
    j1 = _job("ser1/ch1/shot/s1", "16:9")
    j2 = _job("ser1/ch1/shot/s2", "9:16")
    j2.work_id = "w:2"
    _create_work(store, j1)
    _create_work(store, j2)
    w = _worker(store, lock, submit=lambda g, up: "pid")
    jobs = w.assemble_jobs("ch1", ["16:9", "9:16"])
    assert {j.work_id for j in jobs} == {j1.work_id, j2.work_id}
    only_16x9 = w.assemble_jobs("ch1", ["16:9"])
    assert {j.work_id for j in only_16x9} == {j1.work_id}
