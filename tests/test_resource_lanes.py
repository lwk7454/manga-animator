"""P05 — resource lanes, pause/release truth and run controls (§8).

Offline semantics with fake providers: single-mode admission, honest release
observation (unknown ≠ released), pause blocks new admission, drain completes
only after observed release, run-state transitions, no fail-open handoff.
Hardware measurement stays gated on the owner's machine (§24).
"""

from __future__ import annotations

import pytest

from koma.application.control import RunController, can_run_transition
from koma.gpu.resources import (
    ManagedService,
    ReleaseState,
    ResourceManager,
    ResourceMode,
)
from koma.storage.database import Database
from koma.storage.domain_store import KomaStore
from koma.storage.errors import RevisionConflict


class FakeService:
    def __init__(self, service_id: str, up: bool = True):
        self.service_id = service_id
        self.up = up
        self.stops = 0
        self.starts = 0

    def health_probe(self) -> bool:
        return self.up

    def stop(self) -> bool:
        self.stops += 1
        self.up = False
        return True

    def start(self) -> bool:
        self.starts += 1
        self.up = True
        return True


def _manager(comfy: FakeService, llama: FakeService) -> ResourceManager:
    return ResourceManager([
        ManagedService("comfy-local", ResourceMode.COMFY, comfy.health_probe, comfy.stop,
                       start=comfy.start),
        ManagedService("vision-local", ResourceMode.LLM, llama.health_probe, llama.stop,
                       start=llama.start),
    ])


def test_single_mode_admission_blocks_unreleased_other():
    comfy, llama = FakeService("comfy", up=True), FakeService("llama", up=True)
    rm = _manager(comfy, llama)
    rm.admit("comfy-local")
    with pytest.raises(RuntimeError, match="handoff failed"):
        rm.admit("vision-local")  # no fail-open submission


def test_handoff_after_verified_release():
    comfy, llama = FakeService("comfy", up=True), FakeService("llama", up=False)
    rm = _manager(comfy, llama)
    rm.admit("comfy-local")
    obs = rm.release("comfy-local")
    assert obs.release_state is ReleaseState.RELEASED
    rm.admit("vision-local")  # now allowed
    assert rm.active_service == "vision-local"


def test_pause_blocks_new_admission_and_claims():
    comfy, llama = FakeService("comfy", up=False), FakeService("llama")
    rm = _manager(comfy, llama)
    rm.request_pause()
    with pytest.raises(RuntimeError, match="paused"):
        rm.admit("comfy-local")
    rm.resume()
    assert rm.admit("comfy-local")["admitted"] == "comfy-local"


def test_release_observes_not_intends():
    class Sticky(FakeService):
        def stop(self) -> bool:
            self.stops += 1
            return True  # stop "issued" but process keeps serving

    sticky = Sticky("comfy")
    sticky.up = True
    rm = ResourceManager([ManagedService("comfy-local", ResourceMode.COMFY, sticky.health_probe,
                                         sticky.stop)])
    obs = rm.release("comfy-local", attempts=3, delay=0.01)
    assert obs.release_state is ReleaseState.RELEASE_FAILED  # truth, not intent
    assert sticky.stops == 1


def test_probe_failure_during_release_is_unknown():
    class Flaky(FakeService):
        def health_probe(self):
            raise ConnectionError("box unreachable")

    svc = Flaky("comfy")
    rm = ResourceManager([
        ManagedService("comfy-local", ResourceMode.COMFY, svc.health_probe, svc.stop)
    ])
    obs = rm.release("comfy-local")
    assert obs.release_state is ReleaseState.UNKNOWN  # never "released" on doubt


# ── run controls ─────────────────────────────────────────────────────────────


@pytest.fixture()
def run_env(tmp_path):
    db = Database(tmp_path)
    db.initialize()
    store = KomaStore(db)
    store.create_series("demo")
    run = store.create_run("demo", 1, ["16:9", "9:16"], "production", {"fps": 24})
    store.update_run(run["run_id"], status="running")
    comfy = FakeService("comfy", up=True)
    rm = _manager(comfy, FakeService("llama", up=False))
    return store, RunController(store, rm), comfy, run["run_id"]


def test_pause_flow_is_ack_then_observed_release(run_env):
    store, ctl, _comfy, rid = run_env
    ack = ctl.request_pause(rid)
    assert ack["status"] == "pausing" and ack["requested_control"] == "pause"
    # run is pausing while resources are STILL active (§8.1)
    run = store.get_run(rid)
    assert run["status"] == "pausing" and run["resources_state"] == "unknown"
    # drain tail: release + verify + complete
    state = ctl.drain_and_release(rid, "comfy-local")
    assert state == "released"
    run = store.get_run(rid)
    assert run["status"] == "paused"
    assert run["resources_state"] == "released"
    assert run["resources_observed_at"] is not None


def test_pause_cannot_complete_while_releasing(run_env):
    _store, ctl, _comfy, rid = run_env
    ctl.request_pause(rid)
    with pytest.raises(RevisionConflict):
        ctl.complete_pause(rid, "draining")


def test_resume_from_paused(run_env):
    _store, ctl, _comfy, rid = run_env
    ctl.drain_and_release(rid, "comfy-local")
    r = ctl.resume_run(rid)
    assert r["status"] == "running" and r["requested_control"] is None
    assert r["resources_state"] == "active"


def test_run_status_transitions_enforced(run_env):
    _store, ctl, _comfy, rid = run_env
    # running → complete IS legal (all work may already be done); pausing first is normal
    assert can_run_transition("running", "pausing")
    assert can_run_transition("running", "complete")
    assert not can_run_transition("paused", "complete")  # must resume first
    assert can_run_transition("paused", "running")
    assert not can_run_transition("failed", "running")
    assert not can_run_transition("complete", "running")
    with pytest.raises(RevisionConflict):
        ctl.set_run_status(rid, "waiting_review_noop")


def test_unimplemented_backend_reports_pending_not_success(tmp_path):
    """A release op without a real backend must stay pending/unknown (§7.2)."""
    db = Database(tmp_path)
    db.initialize()
    store = KomaStore(db)
    store.create_series("demo")
    run = store.create_run("demo", 1, ["16:9"], "production", {})
    store.update_run(run["run_id"], status="running")

    class NoBackend:
        @staticmethod
        def health_probe():
            raise ConnectionError("no probe available")

        @staticmethod
        def stop():
            return False

    rm = ResourceManager([ManagedService("tts-local", ResourceMode.TTS, NoBackend.health_probe,
                                         NoBackend.stop)])
    obs = rm.release("tts-local")
    assert obs.release_state is ReleaseState.UNKNOWN
    store.update_run(run["run_id"], resources_state="unknown")
    assert store.get_run(run["run_id"])["resources_state"] == "unknown"
