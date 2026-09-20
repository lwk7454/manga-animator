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
            " datetime('now'), datetime('now'))"
        )
        conn.execute(
            "INSERT INTO chapters (id, series_id, number, revision, created_at,"
            " updated_at) VALUES ('ch1', 'ser1', 1, 1, datetime('now'),"
            " datetime('now'))"
        )
    lock = OwnerLock(tmp_path / "lock")
    return store, lock


def _job(scope="ser1/ch1/shot/s1", ratio="16:9", fp="fp:123", **spec):
    defaults = {
        "shot_revision_id": "sr:1",
        "compiled_graph": {"1": {"class_type": "X"}, "136": {"inputs": {}}},
        "upload_plan": [],
        "seed": 42,
        "output_dir": "/tmp/koma-r5-test",
        "compiled_workflow_hash": "h" * 64,
    }
    defaults.update(spec)
    return ShotRatioJob(
        work_id="w:1",
        scope_key=scope,
        stage="motion",
        ratio=ratio,
        input_fingerprint=fp,
        desired_spec=defaults,
    )


def _seed_shot(store, job):
    from koma.storage.shot_store import ShotStore

    shots = ShotStore(store.db)
    with store.db.transaction() as conn:
        found = conn.execute("SELECT id FROM shot_plan_revisions WHERE id='sr:1'").fetchone()
    if not found:
        beat = shots.add_beat("ser1", "ch1", 1, beat_index=1, source_panel_ids=["p1"])
        shot = shots.add_shot_revision(
            "ser1", "ch1", beat_coverage=[beat["beat_id"]], source_panel_ids=["p1"]
        )
        with store.db.transaction() as conn:
            conn.execute(
                "UPDATE shot_plan_revisions SET id='sr:1' WHERE id=?", (shot["shot_revision_id"],)
            )
    with store.db.transaction() as conn:
        found = conn.execute(
            "SELECT id FROM shot_ratio_specs WHERE shot_revision_id='sr:1' AND ratio=?",
            (job.ratio,),
        ).fetchone()
    if not found:
        shots.add_ratio_spec("sr:1", job.ratio, target_width=864, target_height=480)


def _create_work(store, job: ShotRatioJob, *, state="ready"):
    _seed_shot(store, job)
    now = __import__("datetime").datetime.utcnow().isoformat()
    with store.db.transaction() as conn:
        conn.execute(
            "INSERT INTO work_items (id, series_id, chapter_id, scope_key, stage,"
            " ratio, mode, input_fingerprint, desired_spec, state, priority,"
            " created_at, updated_at)"
            " VALUES (?, 'ser1', ?, ?, ?, ?, 'production', ?, ?, ?, 100, ?, ?)",
            (
                job.work_id,
                "ch1",
                job.scope_key,
                job.stage,
                job.ratio,
                job.input_fingerprint,
                _dumps(job.desired_spec),
                state,
                now,
                now,
            ),
        )


def _dumps(obj):
    import json

    return json.dumps(obj)


def _worker(store, lock, *, submit, poll="generated", fetch=None, validate=None):
    return R2VWorker(
        store,
        lock,
        submitter=lambda g, up, context: submit(g, up),
        poller=lambda pid: poll if isinstance(poll, str) else poll(pid),
        fetcher=fetch
        or (
            lambda pid, d: [
                {
                    "kind": "motion",
                    "relative_path": f"{pid}/out.mp4",
                    "sha256": "bb" * 32,
                    "size": 10,
                    "media_metadata": {},
                    "result_metadata": {},
                }
            ]
        ),
        validator=validate or (lambda artifacts, spec: artifacts),
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
        row = conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
    assert row is not None
    return dict(row)


def _work(store, work_id):
    with store.db.transaction() as conn:
        row = conn.execute("SELECT * FROM work_items WHERE id = ?", (work_id,)).fetchone()
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
    assert row["state"] == "running"
    assert "resume to poll" in out.detail
    # the remote prompt id is durably recorded for the resuming poller
    assert row["remote_prompt_id"] == "pid-3"
    # the work is blocked with an honest reason (operator resumes via §9.1a)
    assert _work(store, job.work_id)["state"] == "active"


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
    w = _worker(store, lock, submit=lambda g, up: (_ for _ in ()).throw(ConnectionError("drop")))
    out = w.execute(job)
    attempt_id = out.attempt_id
    # server actually processed it: history has the SAME graph
    import hashlib
    import json

    graph = job.desired_spec["compiled_graph"]
    h = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()
    w.set_reconcile_snapshot(
        {
            "pid-9": {
                "graph": graph,
                "request_identity": _attempt(store, attempt_id)["request_identity"],
                "server_identity": "comfyui",
            }
        }
    )
    # the intent recorded compiled_workflow_hash 'h*32' — override to match:
    with store.db.transaction() as conn:
        conn.execute("UPDATE attempts SET compiled_workflow_hash = ? WHERE id = ?", (h, attempt_id))
    out2 = w.reconcile_uncertain(attempt_id, w.owner_epoch)
    assert out2.status == "accepted" and out2.prompt_id == "pid-9"
    row = _attempt(store, attempt_id)
    assert row["state"] == "running"


def test_reconcile_ambiguous_stays_held(env):
    store, lock = env
    job = _job()
    _create_work(store, job)
    w = _worker(store, lock, submit=lambda g, up: (_ for _ in ()).throw(ConnectionError("x")))
    out = w.execute(job)
    attempt_id = out.attempt_id
    import hashlib
    import json

    graph = job.desired_spec["compiled_graph"]
    h = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()
    with store.db.transaction() as conn:
        conn.execute("UPDATE attempts SET compiled_workflow_hash = ? WHERE id = ?", (h, attempt_id))
    w.set_reconcile_snapshot(
        {
            "pid-a": {
                "graph": graph,
                "request_identity": _attempt(store, attempt_id)["request_identity"],
                "server_identity": "comfyui",
            },
            "pid-b": {
                "graph": graph,
                "request_identity": _attempt(store, attempt_id)["request_identity"],
                "server_identity": "comfyui",
            },
        }
    )
    out2 = w.reconcile_uncertain(attempt_id, w.owner_epoch)
    assert out2.status == "held"  # 2 matches: ambiguous, never guessed
    assert _attempt(store, attempt_id)["state"] == "submission_uncertain"


def test_owner_conflict_detected(env):
    store, lock = env
    job = _job()
    _create_work(store, job)
    # simulate a lock held by another owner via a second OwnerLock instance
    other = OwnerLock(lock.lock_dir)
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
                    " updated_at = datetime('now') WHERE id = ?",
                    (job.work_id,),
                )
        return "generated"

    w = _worker(store, lock, submit=lambda g, up: "pid-4", poll=poll)
    out = w.execute(job)
    assert out.status == "held"
    assert "fingerprint" in out.detail.lower() or "historical" in out.detail.lower()
    row = _attempt(store, out.attempt_id)
    assert row["state"] == "validating"
    assert store.current_output(job.scope_key, job.stage, job.ratio) is None


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


@pytest.mark.parametrize("failure_phase", ["deadline", "poll", "download", "validate"])
def test_restart_resumes_same_attempt_without_second_submission(env, failure_phase):
    store, lock = env
    job = _job(wait_seconds=0)
    _create_work(store, job)
    submitted = []

    def submit(graph, uploads):
        submitted.append(graph)
        return "original-prompt"

    def broken(*args):
        raise ConnectionError("interrupted phase")

    worker = _worker(
        store,
        lock,
        submit=submit,
        poll="generating"
        if failure_phase == "deadline"
        else broken
        if failure_phase == "poll"
        else "generated",
        fetch=broken if failure_phase == "download" else None,
        validate=broken if failure_phase == "validate" else None,
    )
    first = worker.execute(job)
    assert first.status in {"held", "interrupted"}
    resumed = _worker(store, lock, submit=lambda *_: pytest.fail("duplicate submit")).execute(job)
    assert resumed.status == "succeeded", resumed.detail
    assert resumed.attempt_id == first.attempt_id
    assert resumed.prompt_id == "original-prompt"
    assert len(submitted) == 1
    with store.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM shot_candidates").fetchone()[0] == 1


def test_recovered_submitting_attempt_never_resubmits(env):
    store, lock = env
    job = _job()
    _create_work(store, job)
    claim = store.claim_attempt(job.work_id, "dead-owner")
    store.record_attempt_intent(
        claim["attempt_id"],
        "dead-owner",
        request_identity="original",
        seed=42,
        upload_plan=[],
        output_prefix="p",
        compiled_workflow_hash=R2VWorker._graph_hash_of(job.desired_spec["compiled_graph"]),
    )
    store.begin_submission(claim["attempt_id"], "dead-owner")
    out = _worker(store, lock, submit=lambda *_: pytest.fail("unsafe resubmit")).execute(job)
    assert out.status == "held"
    assert _attempt(store, claim["attempt_id"])["state"] == "submission_uncertain"


def test_reconciliation_requires_request_identity_not_just_graph(env):
    store, lock = env
    job = _job()
    _create_work(store, job)
    worker = _worker(store, lock, submit=lambda *_: (_ for _ in ()).throw(ConnectionError()))
    out = worker.execute(job)
    worker.set_reconcile_snapshot(
        {
            "unrelated": {
                "graph": job.desired_spec["compiled_graph"],
                "request_identity": "someone-elses-request",
                "server_identity": "comfyui",
            }
        }
    )
    assert worker.reconcile_uncertain(out.attempt_id, "new-owner").status == "held"


def test_reconciliation_cannot_steal_live_owner(env):
    store, lock = env
    job = _job()
    _create_work(store, job)
    worker = _worker(store, lock, submit=lambda *_: (_ for _ in ()).throw(ConnectionError()))
    out = worker.execute(job)
    epoch = _attempt(store, out.attempt_id)["owner_epoch"]
    owner = lock.acquire("other command", store.db.data_dir)
    try:
        assert worker.reconcile_uncertain(out.attempt_id, "thief").status == "owner_busy"
        assert _attempt(store, out.attempt_id)["owner_epoch"] == epoch
    finally:
        lock.release(owner)


def test_unconfigured_worker_does_not_create_uncertain_attempt(env):
    store, lock = env
    job = _job()
    _create_work(store, job)
    out = R2VWorker(store, lock).execute(job)
    assert out.status == "held" and "not configured" in out.detail
    with store.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0


def test_deadline_remote_blocks_other_generation(env):
    store, lock = env
    one = _job(wait_seconds=0)
    _create_work(store, one)
    _worker(store, lock, submit=lambda *_: "still-running", poll="generating").execute(one)
    two = _job(scope="second", ratio="9:16")
    two.work_id = "w:2"
    _create_work(store, two)
    out = _worker(store, lock, submit=lambda *_: pytest.fail("overlapping remote work")).execute(
        two
    )
    assert out.status == "held"
    assert "outstanding" in out.detail


def test_legacy_deadline_interruption_resumes_known_prompt(env):
    from koma.models.work import AttemptState

    store, lock = env
    job = _job(wait_seconds=0)
    _create_work(store, job)
    first = _worker(store, lock, submit=lambda *_: "original", poll="generating").execute(job)
    row = _attempt(store, first.attempt_id)
    store.record_attempt_outcome(
        first.attempt_id,
        row["owner_epoch"],
        AttemptState.INTERRUPTED,
        error_detail="deadline: remote original continues; resume to poll",
    )
    resumed = _worker(store, lock, submit=lambda *_: pytest.fail("resubmitted")).execute(job)
    assert resumed.status == "succeeded"
    assert resumed.attempt_id == first.attempt_id
