"""ComfyUI GPU worker — submit/poll/download/free_vram (PLAN §9, vidgen doctrine).

Doctrine inherited from vidgen (decision #032/#038/#011/#040):
- poll-\"nowhere on server\" ⇒ FAILED (no zombie QUEUED)
- frame grids live-read from ``/object_info`` (never hardcoded)
- unique per-job output prefixes AND upload names
- ``/free`` (unload_models + free_memory) between heavy phases
"""

from __future__ import annotations

import random
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from ..log import get_logger
from ..models.domain import JobStatus
from ..network import require_local_endpoint
from .sizing import read_frame_grid

log = get_logger("gpu.worker")


class GPUError(RuntimeError):
    """Worker-level failure (HTTP, protocol, or job failure)."""


class ComfyUIWorker:
    """Thin ComfyUI HTTP client for koma's GPU lanes."""

    def __init__(self, host: str, port: int = 8188, timeout: float = 60.0):
        self.base = require_local_endpoint(f"http://{host}:{port}")
        self.host = host
        self.port = port
        self.timeout = timeout

    # ── health / introspection ──────────────────────────────────────────────
    def is_online(self) -> bool:
        try:
            r = httpx.get(
                f"{self.base}/system_stats", timeout=3.0, trust_env=False, follow_redirects=False
            )
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def object_info(self, node_type: str | None = None) -> dict[str, Any]:
        url = f"{self.base}/object_info"
        if node_type:
            url += f"/{node_type}"
        r = httpx.get(url, timeout=self.timeout, trust_env=False, follow_redirects=False)
        r.raise_for_status()
        return r.json()

    def frame_grid(self, node_type: str, length_field: str = "length") -> tuple[int, int]:
        """Live (min, step) of a node's length widget — vidgen #38."""
        return read_frame_grid(self.object_info(node_type), node_type, length_field)

    # ── image upload ────────────────────────────────────────────────────────
    def upload_image(self, path: Path) -> str:
        """Upload a local image; returns the server-side name to inject.

        Name is uniquified per call (vidgen decision #40 — ComfyUI input
        names collide across queued jobs otherwise).
        """
        unique = f"{path.stem}_{uuid.uuid4().hex[:8]}{path.suffix or '.png'}"
        r = httpx.post(
            f"{self.base}/upload/image",
            files={"image": (unique, path.read_bytes())},
            data={"overwrite": "true", "type": "input"},
            timeout=self.timeout,
            trust_env=False,
            follow_redirects=False,
        )
        r.raise_for_status()
        name = r.json().get("name")
        if not name:
            raise GPUError(f"upload/image returned no name: {r.text[:200]}")
        return name

    # ── submit / poll / download ────────────────────────────────────────────
    def submit(self, workflow: dict[str, Any], output_prefix: str) -> str:
        """Queue a workflow (API format); returns the prompt_id.

        ComfyUI returns validation errors as HTTP 400 with a JSON body —
        surfaced as GPUError (never a bare httpx error).
        """
        payload = {"prompt": workflow, "client_id": f"koma-{uuid.uuid4().hex[:8]}"}
        r = httpx.post(
            f"{self.base}/prompt",
            json=payload,
            timeout=self.timeout,
            trust_env=False,
            follow_redirects=False,
        )
        if r.status_code != 200:
            raise GPUError(f"submit failed ({r.status_code}): {r.text}"[:400])
        data = r.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise GPUError(f"submit failed: {data}"[:400])
        log.info("submitted job %s (prefix %s)", prompt_id, output_prefix)
        return prompt_id

    def poll(self, prompt_id: str) -> JobStatus:
        """Current status of a job. nowhere-on-server ⇒ FAILED (vidgen #32)."""
        r = httpx.get(
            f"{self.base}/history/{prompt_id}",
            timeout=self.timeout,
            trust_env=False,
            follow_redirects=False,
        )
        r.raise_for_status()
        history = r.json()
        if prompt_id in history:
            entry = history[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                return JobStatus.FAILED
            if status.get("completed", False) or entry.get("outputs"):
                return JobStatus.GENERATED
            return JobStatus.GENERATING
        # Not in history: check the live queue before declaring it lost.
        q = httpx.get(
            f"{self.base}/queue", timeout=self.timeout, trust_env=False, follow_redirects=False
        ).json()
        ids = {e[1] for e in q.get("queue_running", [])} | {
            e[1] for e in q.get("queue_pending", [])
        }
        if prompt_id in ids:
            return JobStatus.GENERATING
        return JobStatus.FAILED

    def download_output(self, prompt_id: str, output: Path) -> Path:
        """Download the first image output of a completed job."""
        r = httpx.get(
            f"{self.base}/history/{prompt_id}",
            timeout=self.timeout,
            trust_env=False,
            follow_redirects=False,
        )
        r.raise_for_status()
        entry = r.json().get(prompt_id, {})
        outputs = entry.get("outputs", {})
        for _node_id, node_out in outputs.items():
            images = node_out.get("images") or []
            if images:
                img = images[0]
                return self._fetch_view(img, output)
        raise GPUError(f"job {prompt_id} produced no image outputs")

    def download_output_file(self, prompt_id: str, output: Path) -> Path:
        """Download the first output file of any kind (image OR video).

        SaveImage emits ``images``; SaveVideo/VHS emit ``videos``/``gifs``
        with the same filename/subfolder/type shape.
        """
        r = httpx.get(
            f"{self.base}/history/{prompt_id}",
            timeout=self.timeout,
            trust_env=False,
            follow_redirects=False,
        )
        r.raise_for_status()
        entry = r.json().get(prompt_id, {})
        outputs = entry.get("outputs", {})
        for _node_id, node_out in outputs.items():
            for key in ("images", "videos", "gifs", "audio"):
                items = node_out.get(key) or []
                if items:
                    return self._fetch_view(items[0], output)
        raise GPUError(f"job {prompt_id} produced no downloadable outputs")

    def _fetch_view(self, item: dict[str, Any], output: Path) -> Path:
        url = f"{self.base}/view?filename={item['filename']}&type={item.get('type', 'output')}"
        if item.get("subfolder"):
            url += f"&subfolder={item['subfolder']}"
        output.parent.mkdir(parents=True, exist_ok=True)
        content = httpx.get(url, timeout=self.timeout * 4, trust_env=False, follow_redirects=False)
        content.raise_for_status()
        output.write_bytes(content.content)
        log.info("downloaded %s (%d bytes)", output.name, len(content.content))
        return output

    def free_vram(self) -> bool:
        try:
            r = httpx.post(
                f"{self.base}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=self.timeout,
                trust_env=False,
                follow_redirects=False,
            )
            return r.status_code == 200
        except httpx.HTTPError as exc:
            log.warning("free_vram failed: %s", exc)
            return False

    # ── convenience: full colorize job ──────────────────────────────────────
    def run_colorize(
        self,
        workflow_template: dict[str, Any],
        inject: dict[str, str],
        panel_png: Path,
        sheet_png: Path,
        output: Path,
        *,
        width: int = 512,
        height: int = 512,
        steps: int = 25,
        seed: int | None = None,
        poll_interval: float = 3.0,
        timeout_s: float = 900.0,
    ) -> Path:
        """Upload ref sheet + panel, run the MangaNinja workflow, download color.png.

        ``inject`` maps logical slots to node paths, e.g.
        ``{"checkpoint": "12.inputs.checkpoint", "image": "16.inputs.image", ...}``.
        """
        panel_name = self.upload_image(panel_png)
        sheet_name = self.upload_image(sheet_png)
        seed = seed if seed is not None else random.randint(0, 2**31 - 1)

        wf = _deepcopy(workflow_template)
        _set_path(wf, inject["lineart"], panel_name)
        _set_path(wf, inject["ref_image"], sheet_name)
        _set_path(wf, inject["width"], width)
        _set_path(wf, inject["height"], height)
        _set_path(wf, inject["steps"], steps)
        _set_path(wf, inject["seed"], seed)
        prefix = f"koma_color_{uuid.uuid4().hex[:8]}"
        _set_path(wf, inject["output"], prefix)

        prompt_id = self.submit(wf, prefix)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status = self.poll(prompt_id)
            if status is JobStatus.FAILED:
                raise GPUError(f"colorize job {prompt_id} failed on the GPU box")
            if status is JobStatus.GENERATED:
                return self.download_output(prompt_id, output)
            time.sleep(poll_interval)
        raise GPUError(f"colorize job {prompt_id} timed out after {timeout_s}s")


def _deepcopy(wf: dict[str, Any]) -> dict[str, Any]:
    import copy

    return copy.deepcopy(wf)


def _set_path(workflow: dict[str, Any], node_path: str, value: Any) -> None:
    """Set a value at ``"<node_id>.inputs.<field>"`` (or ``"<node_id>.<field>"``)."""
    parts = node_path.split(".")
    if len(parts) == 3 and parts[1] == "inputs":
        node_id, _, field = parts
    elif len(parts) == 2:
        node_id, field = parts
    else:
        raise GPUError(f"inject path must look like '<id>.inputs.<field>': {node_path}")
    if node_id not in workflow:
        raise GPUError(f"workflow has no node {node_id} (for {node_path})")
    workflow[node_id].setdefault("inputs", {})[field] = value
