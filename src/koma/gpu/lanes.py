"""GPU lane arbitration — one GPU consumer at a time (decision #026).

The 3090 is a single shared 24GB resource with TWO resident daemons:

  - ComfyUI   (:8188) — pixel lanes (colorize, outpaint, i2v, upscale)
  - llama.cpp (:11434) — vision lane (captions, speaker ID, palette proposals)

They NEVER legitimately overlap in the pipeline (vision is Stage A, pixels
are Stage B), but both are persistent scheduled tasks, so without arbitration
each behaves as if it owns the GPU. llama-server with -ngl 99 holds ~7-8GB
permanently (keep_alive 30m); a heavy ComfyUI render peaks near 23.6GB —
the two together silently OOM or force slow shared-memory fallback.

Design: llama-server is a LANE, not a resident. `ensure_vision_lane(on=…)`
starts/stops the `koma-llama` scheduled task over SSH, and every lane
consumer calls it before work. `koma doctor` verifies the invariant:
"both GPU consumers loaded" = violation.

SSH access uses KOMA_GPU_* env credentials (never committed), same as the
worker's other box operations.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

from ..log import get_logger

log = get_logger("gpu.lanes")

# default vision endpoint lives on the GPU box (config [vision].base_url)
VISION_HOST_DEFAULT = "192.168.50.103"
LLAMA_TASK = "koma-llama"


class LaneError(RuntimeError):
    """GPU lane arbitration failed."""


def _ssh_target(host: str | None = None) -> str:
    user = os.environ.get("KOMA_GPU_USER", "Admin")
    host = host or VISION_HOST_DEFAULT
    return f"{user}@{host}"


def _vision_host(config: Any = None) -> str:
    """Extract host from config [vision].base_url (LAN-local endpoint)."""
    if config is not None:
        try:
            base = config.vision_base_url
        except AttributeError:
            base = None
        if base and "://" in str(base):
            return str(base).split("://", 1)[1].split(":", 1)[0]
    return VISION_HOST_DEFAULT


def _ssh(host: str, *args: str, timeout: float = 45.0) -> str:
    cmd = ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", _ssh_target(host), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise LaneError(f"ssh timeout talking to {_ssh_target(host)}") from e
    if proc.returncode != 0:
        raise LaneError(f"ssh failed ({proc.returncode}): {proc.stderr.strip()[:200]}")
    return proc.stdout


def llama_running(host: str) -> bool:
    """True if the vision service is UP (answering /health).

    Health is the source of truth, not tasklist: after taskkill the process
    entry lingers in tasklist for seconds while VRAM is already released and
    the socket closed — tasklist lies during async teardown, /health doesn't.
    """
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:11434/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_vision_lane(on: bool, host: str | None = None, config: Any = None) -> dict[str, Any]:
    """Start or stop the vision server so exactly one GPU lane is active.

    Returns {changed, llama_running}. Errors propagate — callers decide
    fail-open (script step keeps captions optional) vs fail-closed.
    """
    host = host or _vision_host(config)
    running = llama_running(host)
    action = "noop"

    if on and not running:
        _ssh(host, "schtasks", "/run", "/tn", LLAMA_TASK, timeout=45)
        action = "started"
        _wait_health(host, want=True)
    elif not on and running:
        _ssh(
            host,
            "taskkill", "/F", "/IM", "llama-server.exe",
            timeout=30,
        )
        action = "stopped"
        _wait_health(host, want=False)
    elif on and running:
        action = "already-running"

    log.info("vision lane %s (was %s)", action, "up" if running else "down")
    # re-probe reality: taskkill/schtasks report intent, not state (process
    # teardown is async on Windows — tasklist can lag a few seconds)
    after = llama_running(host)
    return {"action": action, "llama_running": after}


def _wait_health(host: str, want: bool, attempts: int = 12, delay: float = 5.0) -> None:
    """Block until /health agrees with `want` (or attempts exhausted)."""
    import time
    import urllib.request

    for _ in range(attempts):
        try:
            with urllib.request.urlopen(f"http://{host}:11434/health", timeout=3) as r:
                ok = r.status == 200
        except Exception:
            ok = False
        if ok == want:
            return
        time.sleep(delay)


def check_conflict(host: str | None = None, config: Any = None) -> dict[str, Any]:
    """Doctor check: are BOTH GPU consumers alive at once? (decision #026)"""
    host = host or _vision_host(config)
    llama = llama_running(host)
    comfy = False
    try:
        import urllib.request

        with urllib.request.urlopen(f"http://{host}:8188/system_stats", timeout=5) as r:
            comfy = r.status == 200
    except Exception:
        comfy = False
    conflict = llama and comfy
    return {
        "llama_running": llama,
        "comfy_running": comfy,
        "conflict": conflict,
        "verdict": (
            "VIOLATION — both GPU consumers loaded; llama-server holds ~8GB "
            "that a render may need. Stop the vision lane before rendering."
            if conflict
            else "ok"
        ),
    }


__all__ = ["LaneError", "check_conflict", "ensure_vision_lane", "llama_running"]
