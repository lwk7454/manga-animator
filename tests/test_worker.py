"""Durable feature tests; no live model services."""

from __future__ import annotations

import httpx
import pytest
import respx

from koma.gpu.worker import ComfyUIWorker, GPUError, _set_path
from koma.models.domain import JobStatus


class TestWorker:
    @respx.mock
    def test_submit_returns_prompt_id(self):
        respx.post("http://192.168.50.103:8188/prompt").mock(
            return_value=httpx.Response(200, json={"prompt_id": "abc"})
        )
        w = ComfyUIWorker("192.168.50.103")
        assert w.submit({"1": {"inputs": {}}}, "pre") == "abc"

    @respx.mock
    def test_submit_validation_error_raises(self):
        respx.post("http://192.168.50.103:8188/prompt").mock(
            return_value=httpx.Response(400, json={"error": "bad node"})
        )
        w = ComfyUIWorker("192.168.50.103")
        with pytest.raises(GPUError, match="bad node"):
            w.submit({"1": {"inputs": {}}}, "pre")

    @respx.mock
    def test_poll_nowhere_is_failed(self):
        """vidgen decision #32: nowhere on server ⇒ FAILED (no zombies)."""
        respx.get("http://192.168.50.103:8188/history/p1").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.get("http://192.168.50.103:8188/queue").mock(
            return_value=httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        )
        w = ComfyUIWorker("192.168.50.103")
        assert w.poll("p1") is JobStatus.FAILED

    @respx.mock
    def test_poll_running_in_queue(self):
        respx.get("http://192.168.50.103:8188/history/p1").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.get("http://192.168.50.103:8188/queue").mock(
            return_value=httpx.Response(
                200, json={"queue_running": [[9, "p1"]], "queue_pending": []}
            )
        )
        w = ComfyUIWorker("192.168.50.103")
        assert w.poll("p1") is JobStatus.GENERATING

    @respx.mock
    def test_poll_completed(self):
        respx.get("http://192.168.50.103:8188/history/p1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "p1": {
                        "status": {"status_str": "success", "completed": True},
                        "outputs": {"17": {"images": [{"filename": "x.png", "type": "output"}]}},
                    }
                },
            )
        )
        w = ComfyUIWorker("192.168.50.103")
        assert w.poll("p1") is JobStatus.GENERATED

    @respx.mock
    def test_download_output(self, tmp_path):
        respx.get("http://192.168.50.103:8188/history/p1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "p1": {"outputs": {"17": {"images": [{"filename": "x.png", "type": "output"}]}}}
                },
            )
        )
        respx.get("http://192.168.50.103:8188/view").mock(
            return_value=httpx.Response(200, content=b"PNGDATA")
        )
        w = ComfyUIWorker("192.168.50.103")
        out = tmp_path / "color.png"
        w.download_output("p1", out)
        assert out.read_bytes() == b"PNGDATA"

    @respx.mock
    def test_download_no_outputs_raises(self, tmp_path):
        respx.get("http://192.168.50.103:8188/history/p1").mock(
            return_value=httpx.Response(200, json={"p1": {"outputs": {}}})
        )
        w = ComfyUIWorker("192.168.50.103")
        with pytest.raises(GPUError, match="no image outputs"):
            w.download_output("p1", tmp_path / "c.png")

    @respx.mock
    def test_free_vram(self):
        respx.post("http://192.168.50.103:8188/free").mock(
            return_value=httpx.Response(200, json={})
        )
        w = ComfyUIWorker("192.168.50.103")
        assert w.free_vram() is True

    @respx.mock
    def test_free_vram_offline_is_false(self):
        respx.post("http://192.168.50.103:8188/free").mock(side_effect=httpx.ConnectError("down"))
        w = ComfyUIWorker("192.168.50.103")
        assert w.free_vram() is False

    def test_set_path(self):
        wf = {"12": {"inputs": {"checkpoint": "none"}}}
        _set_path(wf, "12.inputs.checkpoint", "DreamShaper.safetensors")
        assert wf["12"]["inputs"]["checkpoint"] == "DreamShaper.safetensors"

    def test_set_path_rejects_bad_shape(self):
        with pytest.raises(GPUError, match="inject path"):
            _set_path({}, "12", "x")

    def test_set_path_rejects_missing_node(self):
        with pytest.raises(GPUError, match="no node"):
            _set_path({}, "99.inputs.image", "x")
