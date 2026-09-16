"""Managed resource lanes — service handoff, pause/release observation (§8).

Resource modes: ``none, llm, tts, comfy, upscale``. Only one GPU-consuming
mode is admitted at a time by the command owner. Managed services are
CONFIGURED identities (scheduled-task name + verified endpoint + PID
probing), never process-name killing (D07/D08; replaces legacy
taskkill-by-image-name).

Truth rules (§8.1):
- A control request is acknowledged immediately; it is NOT completed until
  the action actually happened. Run state may be ``pausing`` while an
  attempt is still active.
- Resource state is OBSERVED: ``active, draining, releasing, released,
  release_failed, unknown`` with an observation timestamp. Port closure
  alone is not proof of release; unreachable health is ``unknown``,
  never "released".

Hardware qualification (measured VRAM after unload) remains gated on the
owner's machine (§24); offline tests use fake providers with the same
transition contract.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any

from ..log import get_logger

log = get_logger("gpu.lanes")


class ResourceMode(enum.StrEnum):
    NONE = "none"
    LLM = "llm"
    TTS = "tts"
    COMFY = "comfy"
    UPSCALE = "upscale"


class ReleaseState(enum.StrEnum):
    ACTIVE = "active"
    DRAINING = "draining"
    RELEASING = "releasing"
    RELEASED = "released"
    RELEASE_FAILED = "release_failed"
    UNKNOWN = "unknown"


@dataclass
class ManagedService:
    """A configured local service identity (typed, not name-guessed).

    ``health_probe`` returns True when the service is UP and answering.
    ``stop`` performs a scoped stop (configured mechanism — e.g. a scheduled
    task identity or provider unload API). ``pid_probe`` optionally returns
    the service's OS PID for identity verification.
    """

    service_id: str
    mode: ResourceMode
    health_probe: Any  # Callable[[], bool]
    stop: Any  # Callable[[], bool]
    pid_probe: Any = None  # Callable[[], int | None]
    start: Any = None  # Callable[[], bool]


@dataclass
class LaneObservation:
    mode: ResourceMode
    release_state: ReleaseState
    observed_at: float
    evidence: dict = field(default_factory=dict)


class ResourceManager:
    """Admits one GPU-consuming mode; drains/releases through typed services.

    Pause semantics (§8.2): claims stop immediately (caller checks
    ``paused``), the CURRENT attempt finishes, then ``release`` verifies
    actual termination via health re-probe — not intent.
    """

    def __init__(self, services: list[ManagedService] | None = None):
        self.services: dict[str, ManagedService] = {s.service_id: s for s in services or []}
        self._admitted: str | None = None
        self._paused = False

    @property
    def paused(self) -> bool:
        return self._paused

    def request_pause(self) -> None:
        """Record pause intent (acknowledged; drain happens in the run loop)."""
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def admit(self, service_id: str) -> dict:
        """Admit a mode: only one GPU mode at once; drain-then-handoff.

        No fail-open submission after a handoff error (§8.5).
        """
        svc = self.services.get(service_id)
        if svc is None:
            raise KeyError(f"unknown managed service {service_id!r}")
        if self._paused:
            raise RuntimeError("paused: no new service admission")
        if self._admitted and self._admitted != service_id:
            # verify the current mode is actually released before switching
            current = self.services[self._admitted]
            if svc.mode is not ResourceMode.NONE and svc.mode != ResourceMode.NONE:
                if current.health_probe():
                    raise RuntimeError(
                        f"service {self._admitted} ({current.mode.value}) still holds the GPU; "
                        "handoff failed — no fail-open submission"
                    )
        up = svc.health_probe()
        if not up and svc.start is not None:
            up = bool(svc.start())
        if not up:
            raise RuntimeError(f"service {service_id} not healthy after start attempt")
        self._admitted = service_id
        return {"admitted": service_id, "mode": svc.mode.value, "healthy": up}

    def release(
        self, service_id: str, *, attempts: int = 12, delay: float = 0.05
    ) -> LaneObservation:
        """Stop ONLY the configured service; observe until health agrees.

        Returns an honest observation: RELEASED only after the health probe
        confirms down; UNKNOWN when the probe cannot be reached.
        """
        svc = self.services.get(service_id)
        if svc is None:
            raise KeyError(f"unknown managed service {service_id!r}")
        svc.stop()
        ok = False
        for _ in range(attempts):
            try:
                if not svc.health_probe():
                    ok = True
                    break
            except Exception as exc:  # probe itself failing = unknown, not released
                log.warning("health probe failed during release: %s", exc)
                return LaneObservation(
                    svc.mode, ReleaseState.UNKNOWN, time.monotonic(),
                    {"error": str(exc), "service_id": service_id},
                )
            time.sleep(delay)
        state = ReleaseState.RELEASED if ok else ReleaseState.RELEASE_FAILED
        if self._admitted == service_id:
            self._admitted = None
        return LaneObservation(svc.mode, state, time.monotonic(), {"service_id": service_id})

    def observe(self, service_id: str) -> LaneObservation:
        """Current observed state of one managed service (no control)."""
        svc = self.services.get(service_id)
        if svc is None:
            raise KeyError(f"unknown managed service {service_id!r}")
        try:
            up = svc.health_probe()
        except Exception as exc:
            return LaneObservation(svc.mode, ReleaseState.UNKNOWN, time.monotonic(),
                                   {"error": str(exc)})
        return LaneObservation(
            svc.mode, ReleaseState.ACTIVE if up else ReleaseState.RELEASED,
            time.monotonic(), {"service_id": service_id},
        )

    @property
    def active_service(self) -> str | None:
        return self._admitted


def release_state_from_observation(obs: LaneObservation) -> str:
    return obs.release_state.value


__all__ = [
    "LaneObservation",
    "ManagedService",
    "ReleaseState",
    "ResourceManager",
    "ResourceMode",
]
