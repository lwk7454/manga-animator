"""Command ownership: OS lock across data roots + control transport (§7).

One mutating/executing Koma command owns the application on the orchestration
host at a time — including across different ``--data-dir`` values. Exclusivity
is an OS-held advisory file lock in a host-user application-state directory
(NOT per-database: two data roots must not drive the GPU concurrently).
PID/heartbeat metadata is descriptive only; the lock is the proof.

Conflict behavior (§7.2): a conflicting invocation reports the current owner
and exits OWNER_BUSY — it must not kill the owner, wait indefinitely, or
quietly run concurrently. Takeover is an explicit approved control operation;
force termination is separate and requires verified process identity.

The lock file lives under ``~/.koma/`` by default (host-user scope); tests
override the root. All lock operations are cross-platform (macOS/Linux:
fcntl.flock; no network, no daemons).
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..log import get_logger
from ..util import new_id

log = get_logger("scheduler.ownership")

DEFAULT_LOCK_DIR = Path.home() / ".koma"


class OwnershipError(RuntimeError):
    """Owner conflict or lock failure (maps to exit code 5)."""


class OwnerBusy(OwnershipError):
    """Exit-code-5 outcome: conflicting owner; display details, do not kill."""


@dataclass
class CommandOwner:
    """One acquired ownership session. Holds the lock fd until released."""

    owner_uuid: str
    epoch: str
    pid: int
    command: str
    data_dir: str
    lock_path: Path
    fd: int

    def describe(self) -> dict:
        return {
            "owner_uuid": self.owner_uuid,
            "epoch": self.epoch,
            "pid": self.pid,
            "command": self.command,
            "data_dir": self.data_dir,
        }


def _process_identity(pid: int) -> str:
    """Creation identity for takeover validation: start time + exe (best effort)."""
    try:
        # macOS/Linux: ps lstart for creation time; exe via /proc or ps
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart=,comm="],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


class OwnerLock:
    """Host-user OS advisory lock + rendezvous metadata file."""

    def __init__(self, lock_dir: Path | None = None):
        self.lock_dir = Path(lock_dir) if lock_dir else DEFAULT_LOCK_DIR

    @property
    def lock_path(self) -> Path:
        return self.lock_dir / "owner.lock"

    @property
    def info_path(self) -> Path:
        """Rendezvous metadata: identifies the owning data root's DB + owner.

        Operational discovery only — never a second domain store. Written
        atomically and validated against the real lock holder.
        """
        return self.lock_dir / "owner.json"

    def _ensure_dir(self) -> None:
        self.lock_dir.mkdir(parents=True, exist_ok=True)

    def read_info(self) -> dict | None:
        try:
            return json.loads(self.info_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_info(self, owner: CommandOwner) -> None:
        payload = {**owner.describe(), "started_at": _utcnow(), "host_id": _host_id()}
        tmp = self.info_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self.info_path)

    def acquire(
        self,
        command: str,
        data_dir: Path,
        *,
        wait_s: float = 0.0,
    ) -> CommandOwner:
        """Acquire the host lock non-blockingly; raises OwnerBusy-like error."""
        self._ensure_dir()
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        deadline = _monotonic() + wait_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if _monotonic() >= deadline:
                    os.close(fd)
                    info = self.read_info() or {}
                    raise OwnerBusy(
                        "another koma command owns the application: "
                        f"{info.get('command', '?')} pid={info.get('pid', '?')} "
                        f"data={info.get('data_dir', '?')} "
                        f"started={info.get('started_at', '?')}"
                    ) from None
                _sleep(0.1)
        owner = CommandOwner(
            owner_uuid=new_id(),
            epoch=new_id(),
            pid=os.getpid(),
            command=command,
            data_dir=str(data_dir),
            lock_path=self.lock_path,
            fd=fd,
        )
        os.truncate(fd, 0)
        os.write(fd, json.dumps(owner.describe()).encode())
        self._write_info(owner)
        return owner

    def release(self, owner: CommandOwner) -> None:
        try:
            fcntl.flock(owner.fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            # only the holder clears the rendezvous info
            info = self.read_info()
            if info and info.get("owner_uuid") == owner.owner_uuid:
                self.info_path.unlink(missing_ok=True)
        finally:
            os.close(owner.fd)

    def current_holder(self) -> dict | None:
        """Read-only view of who owns the app right now (for status/conflicts)."""
        if not self.lock_path.exists():
            return None
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return None  # nobody holds it (stale file is fine)
        except OSError:
            return self.read_info()
        finally:
            os.close(fd)


# ── time/host helpers (small, module-local; no framework) ────────────────────


def _monotonic() -> float:
    import time

    return time.monotonic()


def _sleep(s: float) -> None:
    import time

    time.sleep(s)


def _utcnow() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _host_id() -> str:
    node = os.uname().nodename
    return node


def validate_takeover_approval(
    approval: dict, holder: dict | None
) -> None:
    """Verify an approval token targets the EXACT current owner (§7.2).

    A generic ``--yes`` is not enough: the approval must carry the expected
    owner UUID and epoch. Raises OwnerBusy on mismatch.
    """
    if not holder:
        return  # nothing to take over
    if approval.get("owner_uuid") != holder.get("owner_uuid"):
        raise OwnerBusy("approval targets a different owner UUID (owner changed?)")
    if approval.get("epoch") != holder.get("epoch"):
        raise OwnerBusy("approval epoch is stale (owner restarted since request)")


def force_stop_pid(pid: int, expected_identity: str, *, executable_hint: str = "") -> bool:
    """Explicit force-stop with identity verification (§7.2, last resort).

    Validates PID + process creation identity; never kills by name; never
    killall. Returns True when the signal was sent to a verified process.
    """
    if pid <= 0:
        raise OwnershipError("refusing to signal: no verified PID")
    identity = _process_identity(pid)
    if not identity:
        raise OwnershipError(f"cannot verify process identity for pid {pid}")
    if expected_identity and expected_identity not in identity:
        raise OwnershipError(
            f"process identity changed since validation ({identity[:60]!r}); refusing to signal"
        )
    try:
        os.kill(pid, 15)  # SIGTERM first, not SIGKILL
    except ProcessLookupError:
        return True
    except PermissionError as exc:
        raise OwnershipError(f"signal denied for pid {pid}: {exc}") from exc
    return True


__all__ = [
    "DEFAULT_LOCK_DIR",
    "CommandOwner",
    "OwnerBusy",
    "OwnerLock",
    "OwnershipError",
    "force_stop_pid",
    "validate_takeover_approval",
]
