"""Phase 3 tests — GPU worker, adapters registry, colorize step, scheduler.

Worker tests use respx (mocked HTTP); colorize/scheduler tests use fake
stores/workers. Live box verification happens via `koma doctor` (manual).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from koma.gpu.sizing import MINIMAX_H3_GRID, align_frame_count, clip_frames, read_frame_grid
from koma.gpu.worker import ComfyUIWorker, GPUError, _set_path
from koma.models.domain import JobStatus, PanelState, ReadingDirection

# ── sizing (17n+5 doctrine) ──────────────────────────────────────────────────


class TestSizing:
    def test_read_grid_from_object_info(self):
        info = {
            "SomeNode": {
                "input": {"required": {"length": [56, {"min": 5, "max": 362, "step": 17}]}}
            }
        }
        assert read_frame_grid(info, "SomeNode") == (5, 17)

    def test_align_snaps_up(self):
        grid = (5, 17)
        assert align_frame_count(5, grid) == 5
        assert align_frame_count(6, grid) == 22
        assert align_frame_count(22, grid) == 22
        assert align_frame_count(23, grid) == 39

    def test_clip_frames_audio_first(self):
        # 4.67s * 24fps = 112.08 → ceil 113 → snap up to 124 (17n+5)
        assert clip_frames(4.666, 24, MINIMAX_H3_GRID) == 124

    def test_read_grid_missing_node_raises(self):
        with pytest.raises(ValueError):
            read_frame_grid({}, "Nope")


# ── worker (respx-mocked HTTP) ───────────────────────────────────────────────


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


# ── adapters registry ────────────────────────────────────────────────────────


class TestAdapters:
    def test_load_real_colorize_profile(self):
        from koma.adapters.registry import list_profiles, load_profile, resolve_profile

        cdir = Path(__file__).parent.parent / "config"
        profs = list_profiles(cdir)
        assert any(p["id"] == "manganinja_sd15" and p["kind"] == "colorize" for p in profs)

        spec = load_profile(cdir, "manganinja_sd15")
        assert spec["kind"] == "colorize"
        resolved = resolve_profile(cdir, "manganinja_sd15")
        wf = resolved["workflow"]
        # every inject path points at an existing node in the workflow
        for key, path in resolved["inject"].items():
            node = path.split(".")[0]
            assert node in wf, f"inject {key} → missing node {node}"

    def test_missing_profile_raises(self, tmp_path):
        from koma.adapters.registry import AdapterError, load_profile

        with pytest.raises(AdapterError, match="not found"):
            load_profile(tmp_path, "nope")

    def test_missing_workflow_raises(self, tmp_path):
        from koma.adapters.registry import AdapterError, resolve_profile

        (tmp_path / "models" / "profiles").mkdir(parents=True)
        (tmp_path / "models" / "profiles" / "p.toml").write_text(
            'id = "p"\nkind = "colorize"\nworkflow = "workflows/ghost.json"\n'
        )
        with pytest.raises(AdapterError, match="workflow file missing"):
            resolve_profile(tmp_path, "p")


# ── scheduler queue + pause semantics ────────────────────────────────────────


class TestQueue:
    def test_pause_flag_roundtrip(self, tmp_path):
        from koma.scheduler.queue import is_paused, set_paused

        assert not is_paused(tmp_path)
        set_paused(tmp_path, True)
        assert is_paused(tmp_path)
        set_paused(tmp_path, False)
        assert not is_paused(tmp_path)

    def test_queue_fifo(self, tmp_path):
        from koma.scheduler.queue import load_queue, next_pending, save_queue

        q = load_queue(tmp_path)
        q["jobs"] = [
            {"id": "a", "state": "done"},
            {"id": "b", "state": "pending"},
            {"id": "c", "state": "pending"},
        ]
        save_queue(tmp_path, q)
        assert next_pending(load_queue(tmp_path))["id"] == "b"

    def test_running_job(self, tmp_path):
        from koma.scheduler.queue import load_queue, running_job

        q = load_queue(tmp_path)
        q["jobs"] = [{"id": "a", "state": "running"}]
        assert running_job(q)["id"] == "a"


# ── colorize step (fake store end-to-end, no HTTP) ───────────────────────────


@pytest.fixture
def mini_series(tmp_path, monkeypatch):
    """A tiny series with one SCRIPTED panel + card registry, config pointed at tmp."""
    from koma.models.domain import PanelState
    from koma.storage.series_store import SeriesStore

    data = tmp_path / "data"
    store = SeriesStore(data)
    store.create_series("testseries", direction=ReadingDirection.RTL, language="ja")
    # inject one page with one panel directly at SCRIPTED
    from koma.models.domain import BBox, Page, Panel

    page = Page(
        chapter=1,
        page_number=1,
        source_path="ch01/p001.jpg",
        width=1000,
        height=1400,
        direction_resolved=ReadingDirection.RTL,
        panels=[
            Panel(
                panel_id="ch01_p001_01",
                bbox=BBox(x=0, y=0, w=500, h=700),
                state=PanelState.SCRIPTED,
            ),
        ],
    )
    store.save_pages("testseries", [page])
    # script.json
    ch_dir = store.chapter_dir("testseries", 1)
    (ch_dir / "panels" / "ch01_p001_01").mkdir(parents=True)
    (ch_dir / "panels" / "ch01_p001_01" / "panel.png").write_bytes(b"PNG")
    (ch_dir / "script.json").write_text(
        json.dumps(
            {
                "panels": {
                    "ch01_p001_01": {
                        "panel_id": "ch01_p001_01",
                        "caption": "a girl in a garden",
                        "characters": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return store


class TestColorizeStep:
    def test_queue_and_reconcile_happy_path(self, mini_series, tmp_path, monkeypatch):
        from koma.steps import colorize as cz

        fake_settings = type(
            "S",
            (),
            {
                "resolved_config_dir": lambda self: Path(__file__).parent.parent / "config",
            },
        )()

        result = cz.queue_colorize("testseries", mini_series, fake_settings)
        assert result["queued"] == 1
        # panel STAYS SCRIPTED at queue time (decision #028: no queue-time
        # state flips — the monitor advances states only on download)
        _pg, panel = mini_series.find_panel("testseries", "ch01_p001_01")
        assert panel.state is PanelState.SCRIPTED

        # idempotent: job dedupe by id — second run adds nothing new
        again = cz.queue_colorize("testseries", mini_series, fake_settings)
        assert again["queued"] == 0 and again["skipped"] == 1
        _pg, panel = mini_series.find_panel("testseries", "ch01_p001_01")
        assert panel.state is PanelState.SCRIPTED  # unchanged

        # reconcile with a fake worker: job is pending w/o prompt_id → submitted
        class FakeWorker:
            def __init__(self):
                self.downloads = []

            def poll(self, pid):
                return JobStatus.GENERATED

            def download_output(self, pid, out):
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b"COLOR")
                self.downloads.append(pid)

        fw = FakeWorker()
        res = cz.reconcile_colorize("testseries", mini_series, fake_settings, fw)
        # no prompt_id yet → nothing to poll; monitor owns submission
        assert res == {"downloaded": 0, "failed": 0}

    def test_color_source_series_skips_gpu(self, tmp_path):
        """A color-source series never queues GPU jobs: color.png = panel.png
        and panels jump straight to COLORIZED (PLAN §7 skip-forward)."""
        from koma.models.domain import BBox, Page, Panel, PanelState, ReadingDirection
        from koma.steps import colorize as cz
        from koma.storage.series_store import SeriesStore

        data = tmp_path / "data"
        store = SeriesStore(data)
        store.create_series("colorseries", direction=ReadingDirection.RTL, language="ja")
        # flip the series to color source
        series = store.load_series("colorseries")
        series.meta.is_color_source = True
        store.save_series(series)

        page = Page(
            chapter=1,
            page_number=1,
            source_path="ch01/p001.jpg",
            width=1000,
            height=1400,
            direction_resolved=ReadingDirection.RTL,
            panels=[
                Panel(
                    panel_id="ch01_p001_01",
                    bbox=BBox(x=0, y=0, w=500, h=700),
                    state=PanelState.SCRIPTED,
                ),
            ],
        )
        store.save_pages("colorseries", [page])
        ch_dir = store.chapter_dir("colorseries", 1)
        (ch_dir / "panels" / "ch01_p001_01").mkdir(parents=True)
        (ch_dir / "panels" / "ch01_p001_01" / "panel.png").write_bytes(b"PANEL")
        (ch_dir / "script.json").write_text(
            json.dumps(
                {
                    "panels": {
                        "ch01_p001_01": {
                            "panel_id": "ch01_p001_01",
                            "caption": "x",
                            "characters": [],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        fake_settings = type(
            "S", (), {"resolved_config_dir": lambda self: Path(__file__).parent.parent / "config"}
        )()
        result = cz.queue_colorize("colorseries", store, fake_settings)
        assert result["queued"] == 0
        assert result["skipped"] == 1
        color = ch_dir / "panels" / "ch01_p001_01" / "color.png"
        assert color.read_bytes() == b"PANEL"  # copied, not generated
        _pg, panel = store.find_panel("colorseries", "ch01_p001_01")
        assert panel.state is PanelState.COLORIZED
