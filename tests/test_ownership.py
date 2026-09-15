"""P02 — command ownership, exclusion and takeover validation (§7).

Real exclusion is tested with a subprocess holding the lock (not sleeps):
the parent must see OWNER_BUSY and must NOT terminate the holder.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from koma.scheduler.ownership import (
    OwnerBusy,
    OwnerLock,
    force_stop_pid,
    validate_takeover_approval,
)
from koma.storage.database import Database
from koma.storage.domain_store import KomaStore
from koma.storage.errors import RevisionConflict


@pytest.fixture()
def lock(tmp_path: Path) -> OwnerLock:
    return OwnerLock(tmp_path / "lockdir")


_SUBPROBE = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from koma.scheduler.ownership import OwnerLock
    lock = OwnerLock(Path(sys.argv[1]))
    owner = lock.acquire("run", Path(sys.argv[2]))
    print(owner.owner_uuid, flush=True)
    time.sleep(30)
    """
)


def _hold_in_subprocess(lock_dir: Path, data_dir: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", _SUBPROBE, str(lock_dir), str(data_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_two_processes_cannot_both_own(tmp_path: Path):
    """The §7.2 acceptance: two CLI processes cannot both own execution."""
    lock_dir = tmp_path / "ld"
    data = tmp_path / "data"
    proc = _hold_in_subprocess(lock_dir, data)
    assert proc.stdout is not None and proc.stderr is not None
    try:
        holder_line = proc.stdout.readline().strip()
        assert holder_line, "subprocess failed to acquire: " + proc.stderr.read()
        # competing acquire from THIS process must be refused with details
        competitor = OwnerLock(lock_dir)
        with pytest.raises(OwnerBusy) as exc:
            competitor.acquire("monitor", data)
        assert "owns the application" in str(exc.value)
        # the holder process must still be alive (no collateral kill)
        assert proc.poll() is None
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_acquire_release_reacquire(tmp_path: Path):
    lock = OwnerLock(tmp_path)
    o1 = lock.acquire("run", tmp_path / "a")
    holder = lock.current_holder()
    assert holder is not None
    assert holder["owner_uuid"] == o1.owner_uuid
    lock.release(o1)
    assert lock.current_holder() is None
    o2 = lock.acquire("run", tmp_path / "b")
    assert o2.owner_uuid != o1.owner_uuid
    lock.release(o2)


def test_takeover_approval_targets_exact_owner(tmp_path: Path):
    lock = OwnerLock(tmp_path)
    owner = lock.acquire("run", tmp_path)
    holder = lock.current_holder()
    validate_takeover_approval(
        {"owner_uuid": owner.owner_uuid, "epoch": owner.epoch}, holder
    )
    with pytest.raises(OwnerBusy):
        validate_takeover_approval({"owner_uuid": "x", "epoch": owner.epoch}, holder)
    with pytest.raises(OwnerBusy):
        validate_takeover_approval(
            {"owner_uuid": owner.owner_uuid, "epoch": "stale"}, holder
        )
    lock.release(owner)


def test_no_approval_needed_when_no_holder(tmp_path: Path):
    validate_takeover_approval({"anything": 1}, None)


def test_force_stop_refuses_unverifiable(tmp_path: Path):
    from koma.scheduler.ownership import OwnershipError

    with pytest.raises(OwnershipError):
        force_stop_pid(0, "")
    with pytest.raises(OwnershipError):
        force_stop_pid(2_000_000_000, "id")


def test_command_owner_metadata_roundtrip(tmp_path: Path):
    db = Database(tmp_path)
    db.initialize()

    store = KomaStore(db)
    assert store.read_command_owner() is None
    store.write_command_owner(
        {"owner_uuid": "u1", "pid": 42, "epoch": "e1", "command_id": "run", "run_id": "r1"}
    )
    row = store.read_command_owner()
    assert row is not None
    assert row["owner_uuid"] == "u1" and row["run_id"] == "r1"
    store.clear_command_owner("u1")
    assert store.read_command_owner() is None
    # only the recorded owner clears it
    store.write_command_owner({"owner_uuid": "u2", "pid": 43, "epoch": "e2"})
    store.clear_command_owner("u1")  # wrong owner: no effect
    assert store.read_command_owner()["owner_uuid"] == "u2"


def test_control_request_targets_owner_epoch(tmp_path: Path):
    db = Database(tmp_path)
    db.initialize()

    store = KomaStore(db)
    store.write_command_owner({"owner_uuid": "u1", "pid": 1, "epoch": "e1"})
    store.request_control("u1", "e1", "pause", "op-1")
    assert len(store.pending_control_requests("u1", "e1")) == 1
    assert store.pending_control_requests("u1", "e2") == []
    with pytest.raises(RevisionConflict):
        store.acknowledge_control("op-1", "u1", "e2")
    store.acknowledge_control("op-1", "u1", "e1")
    store.complete_control("op-1", {"drained": True})
    assert store.pending_control_requests("u1", "e1") == []
    # idempotent completion
    store.complete_control("op-1", {"drained": True})
