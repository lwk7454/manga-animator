"""Regression tests for the September 2026 review; no model calls."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from koma.adapters.registry import AdapterError, resolve_profile, verify_component
from koma.adapters.ui2api import UIWorkflow, WorkflowConvertError
from koma.cli import app
from koma.config import Settings
from koma.models.domain import BBox, Page, Panel, PanelState, ReadingDirection
from koma.scheduler.queue import load_queue, save_queue, set_paused
from koma.storage import SeriesStore, default_data_dir

CONFIG = Path(__file__).resolve().parents[1] / "config"


@pytest.mark.parametrize("paused", [False, True])
def test_monitor_drains_last_running_job(tmp_path, monkeypatch, paused):
    import koma.scheduler.monitor as monitor_mod

    save_queue(tmp_path, {"jobs": [{"id": "one", "state": "running" if paused else "pending"}]})
    set_paused(tmp_path, paused)
    ticks = []

    def tick(*args, **kwargs):
        ticks.append(1)
        assert len(ticks) <= 2, "monitor did not stop after draining"
        q = load_queue(tmp_path)
        q["jobs"][0]["state"] = "running" if len(ticks) == 1 else "downloaded"
        save_queue(tmp_path, q)
        return dict(submitted=int(len(ticks) == 1), downloaded=int(len(ticks) == 2),
                    failed=0, paused=paused, box_online=True)

    monkeypatch.setattr(monitor_mod, "monitor_once", tick)
    result = CliRunner().invoke(app, ["monitor", "--data-dir", str(tmp_path), "--interval", "0"])
    assert result.exit_code == 0, result.output
    assert len(ticks) == 2
    assert load_queue(tmp_path)["jobs"][0]["state"] == "downloaded"


def test_monitor_once_still_returns_with_work_running(tmp_path, monkeypatch):
    import koma.scheduler.monitor as monitor_mod

    save_queue(tmp_path, {"jobs": [{"id": "one", "state": "running"}]})
    tick = Mock(return_value=dict(submitted=0, downloaded=0, failed=0,
                                 paused=False, box_online=True))
    monkeypatch.setattr(monitor_mod, "monitor_once", tick)
    result = CliRunner().invoke(app, ["monitor", "--once", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert tick.call_count == 1
    assert "queue drained" not in result.output


@pytest.mark.parametrize("profile", ["minimax_h3_fl2va_turbo", "minimax_h3_fused_turbo"])
@pytest.mark.parametrize("ratio,size", [("16:9", (768, 432)), ("9:16", (432, 768))])
def test_render_compiles_shipped_profiles(profile, ratio, size):
    from koma.steps.render import _submit_render_job

    captured = {}

    def submit(workflow, prefix):
        captured.update(workflow)
        return "remote-id"

    worker = SimpleNamespace(upload_image=lambda path: "uploaded.png",
                             frame_grid=lambda node: (5, 17), submit=submit)
    job = dict(id="test", profile=profile, keyframe_png="test.png", ratio=ratio,
               width=size[0], height=size[1], audio_duration_s=4, motion_prompt="Pan left.")
    _submit_render_job(CONFIG, worker, {}, job)
    node, loader, noise, scheduler = (("7", "6", "8", "11") if "fl2va" in profile
                                     else ("131", "901", "129", "124"))
    assert captured[node]["inputs"]["width"] == size[0]
    assert captured[node]["inputs"]["height"] == size[1]
    assert captured[node]["inputs"]["length"] == 107
    assert captured[node]["inputs"]["first_frame"] == [loader, 0]
    assert captured[loader]["inputs"]["image"] == "uploaded.png"
    assert isinstance(captured[noise]["inputs"]["noise_seed"], int)
    assert captured[scheduler]["inputs"]["steps"] == 4
    assert job["state"] == "running" and job["prompt_id"] == "remote-id"


def test_actual_default_profile_resolves():
    assert resolve_profile(CONFIG, Settings().video_profile_default)["spec"]["kind"] == "video_i2v"


@pytest.mark.parametrize("broken", ["missing_seed", "image_connection"])
def test_invalid_video_binding_rejected_before_queue(tmp_path, broken):
    import shutil

    cfg = tmp_path / "custom-config"
    shutil.copytree(CONFIG, cfg)
    profile = cfg / "models/profiles/minimax_h3_fl2va_turbo.toml"
    text = profile.read_text()
    if broken == "missing_seed":
        text = text.replace('seed     = "8.inputs.noise_seed"\n', '')
    else:
        text = text.replace('keyframe = "6.inputs.image"', 'keyframe = "7.inputs.first_frame"')
    profile.write_text(text)
    with pytest.raises(AdapterError):
        resolve_profile(cfg, "minimax_h3_fl2va_turbo")


def test_custom_config_directory_is_honored(tmp_path):
    cfg = tmp_path / "custom"
    (cfg / "models/profiles").mkdir(parents=True)
    (cfg / "models/profiles/p.toml").write_text('workflow = "graph.json"\n')
    (cfg / "graph.json").write_text('{}')
    assert resolve_profile(cfg, "p")["workflow"] == {}


def test_component_verification_requires_file_and_hash(tmp_path):
    cfg = tmp_path / "config"
    (cfg / "models/components").mkdir(parents=True)
    spec = cfg / "models/components/c.toml"
    model_root = tmp_path / "weights"
    model_root.mkdir()
    weights = model_root / "model.bin"
    digest = hashlib.sha256(b"weights").hexdigest()
    spec.write_text(f'file = "model.bin"\nsha256 = "{digest}"\n')
    assert verify_component(cfg, "c") is False
    with pytest.raises(AdapterError, match="missing"):
        verify_component(cfg, "c", str(model_root))
    weights.write_bytes(b"weights")
    assert verify_component(cfg, "c", str(model_root)) is True
    weights.write_bytes(b"wrong")
    with pytest.raises(AdapterError, match="mismatch"):
        verify_component(cfg, "c", str(model_root))
    spec.write_text('file = "model.bin"\nsha256 = ""\n')
    assert verify_component(cfg, "c", str(model_root)) is False
    spec.write_text('file = "model.bin"\nsha256 = "invalid"\n')
    with pytest.raises(AdapterError, match="invalid"):
        verify_component(cfg, "c", str(model_root))


def test_storage_default_and_script_ceiling():
    from koma.steps.script import _frames

    assert default_data_dir() == Settings().resolved_data_dir()
    assert _frames(4) == 107
    for duration in (0.1, 2, 4, 4.666, 14):
        assert _frames(duration) >= duration * 24
        assert (_frames(duration) - 5) % 17 == 0
    assert _frames(4, grid_fn=lambda seconds: 123) == 123


@pytest.mark.parametrize("kind,mode", [("Bypass", 4), ("Reroute", 0)])
def test_converter_detects_cycles(kind, mode):
    wf = UIWorkflow({"nodes": [{"id": 1, "type": kind, "mode": mode,
                               "inputs": [{"type": "IMAGE", "link": 1}],
                               "outputs": [{"type": "IMAGE"}]}],
                     "links": [[1, 1, 0, 1, 0, "IMAGE"]]})
    with pytest.raises(WorkflowConvertError, match="cycle"):
        wf.real_source(1, 0)


def test_converter_follows_long_acyclic_bypass_chain():
    nodes = [{"id": n, "type": "Bypass", "mode": 4,
              "inputs": [{"type": "IMAGE", "link": n}],
              "outputs": [{"type": "IMAGE"}]} for n in range(1, 25)]
    nodes.append({"id": 25, "type": "LoadImage"})
    links = [[n, n + 1, 0, n, 0, "IMAGE"] for n in range(1, 25)]
    assert UIWorkflow({"nodes": nodes, "links": links}).real_source(1, 0) == (25, 0)


def make_series(tmp_path, state):
    store = SeriesStore(tmp_path / "data")
    store.create_series("demo", ratios=["9:16"])
    pages = []
    for ch in (1, 100):
        pid = f"ch{ch:02d}_p001_01"
        pages.append(Page(chapter=ch, page_number=1, source_path="source.png", width=10,
                          height=10, direction_resolved=ReadingDirection.RTL,
                          panels=[Panel(panel_id=pid, order=1, state=state,
                                        bbox=BBox(x=0, y=0, w=10, h=10))]))
        pdir = store.chapter_dir("demo", ch) / "panels" / pid
        pdir.mkdir(parents=True)
        for name in ("panel.png", "keyframe_1024x576.png"):
            (pdir / name).write_bytes(b"test")
        (store.chapter_dir("demo", ch) / "script.json").write_text(
            json.dumps({"panels": {pid: {"caption": "test"}}}))
    store.save_pages("demo", pages)
    cfg = Settings()
    cfg._data = {"paths": {"data": str(store.data_dir), "config": str(CONFIG)}}
    return store, cfg


def test_keyframes_series_ratios_and_chapter_100(tmp_path):
    from koma.steps.keyframe import queue_keyframes

    store, cfg = make_series(tmp_path, PanelState.COLORIZED)
    assert queue_keyframes("demo", store, cfg, chapter=100)["queued"] == 1
    job, = load_queue(store.data_dir)["jobs"]
    assert job["chapter"] == 100 and job["ratio"] == "9:16"
    assert "/100/" in job["output_png"]


def test_keyframes_cli_forwards_chapter(tmp_path, monkeypatch):
    import koma.steps.keyframe as keyframe

    store, _ = make_series(tmp_path, PanelState.COLORIZED)
    planner = Mock(return_value=dict(queued=0, skipped=0, ratios=["9:16"], profile="test"))
    monkeypatch.setattr(keyframe, "queue_keyframes", planner)
    result = CliRunner().invoke(app, ["keyframes", "demo", "--chapter", "100",
                                     "--data-dir", str(store.data_dir)])
    assert result.exit_code == 0, result.output
    assert planner.call_args.kwargs["chapter"] == 100


@pytest.mark.parametrize("color_source", [False, True])
def test_colorize_chapter_100_and_color_copy_filter(tmp_path, color_source):
    from koma.steps.colorize import queue_colorize

    store, cfg = make_series(tmp_path, PanelState.SCRIPTED)
    series = store.load_series("demo")
    series.meta.is_color_source = color_source
    store.save_series(series)
    result = queue_colorize("demo", store, cfg, chapter=100)
    assert store.find_panel("demo", "ch01_p001_01")[1].state == PanelState.SCRIPTED
    if color_source:
        assert result["skipped_panels"] == ["ch100_p001_01"]
    else:
        job, = load_queue(store.data_dir)["jobs"]
        assert job["chapter"] == 100 and "/100/" in job["panel_png"]


def test_render_chapter_100(tmp_path):
    from koma.steps.render import queue_render

    store, cfg = make_series(tmp_path, PanelState.KEYFRAMED)
    assert queue_render("demo", store, cfg, chapter=100)["queued"] == 1
    job, = load_queue(store.data_dir)["jobs"]
    assert job["chapter"] == 100 and "/100/" in job["output_mp4"]


@pytest.mark.parametrize("stage", ["keyframe", "render"])
def test_unknown_ratio_does_not_write_queue(tmp_path, stage):
    from koma.steps.keyframe import queue_keyframes
    from koma.steps.render import queue_render

    store, cfg = make_series(tmp_path, PanelState.COLORIZED)
    with pytest.raises(ValueError, match="unsupported ratio"):
        if stage == "keyframe":
            queue_keyframes("demo", store, cfg, ratios=["16:9", "1:1"])
        else:
            queue_render("demo", store, cfg, ratio="1:1")
    assert load_queue(store.data_dir) == {"jobs": []}


@pytest.mark.parametrize("answer,expected", [("ch01_p001_02", "ch01_p001_02"),
                                           ("ch01_p001_01", None),
                                           ("ch99_p001_01", None)])
def test_canonical_labels_follow_existing_images(tmp_path, monkeypatch, answer, expected):
    import koma.steps.cards as cards
    from koma.models.domain import CharacterCard

    store, cfg = make_series(tmp_path, PanelState.SCRIPTED)
    store.ensure_chapter("demo", 1)
    store.upsert_card("demo", CharacterCard(id="character", identity={"hair": "silver braided"}))
    chapter = store.chapter_dir("demo", 1)
    first, second = "ch01_p001_01", "ch01_p001_02"
    (chapter / "script.json").write_text(json.dumps({"panels": {
        first: {"caption": "silver braided"}, second: {"caption": "silver braided"}}}))
    (chapter / "panels" / first / "panel.png").unlink()
    second_image = chapter / "panels" / second / "panel.png"
    second_image.parent.mkdir()
    second_image.write_bytes(b"test")
    vision = Mock()
    vision.chat.return_value = answer
    monkeypatch.setattr(cards, "_vision_client", lambda config: vision)
    result = cards.pick_canonical_panels(store, "demo", cfg)["character"]
    assert result["canonical"] == expected
    assert result["candidates"] == [second]
    assert vision.chat.call_args.kwargs["images"] == [second_image]
    prompt = vision.chat.call_args.args[1][0]["content"]
    assert f"1. {second}" in prompt and first not in prompt


def test_offline_monitor_leaves_running_work_untouched(tmp_path):
    from koma.scheduler.monitor import monitor_once

    store, cfg = make_series(tmp_path, PanelState.COLORIZED)
    queue = {"jobs": [{"id": "one", "state": "running", "prompt_id": "remote"}]}
    save_queue(store.data_dir, queue)
    worker = Mock()
    worker.is_online.return_value = False
    summary = monitor_once(store.data_dir, cfg, store, worker=worker)
    assert summary["box_online"] is False
    worker.poll.assert_not_called()
    assert load_queue(store.data_dir) == queue


def test_empty_monitor_does_not_stop_vision(tmp_path, monkeypatch):
    from koma.scheduler.monitor import monitor_once

    store, cfg = make_series(tmp_path, PanelState.COLORIZED)
    lane = Mock()
    monkeypatch.setattr("koma.gpu.lanes.ensure_vision_lane", lane)
    worker = Mock()
    worker.is_online.return_value = True
    monitor_once(store.data_dir, cfg, store, worker=worker)
    lane.assert_not_called()


@pytest.mark.parametrize("handoff", [RuntimeError("ssh failed"), {"llama_running": True},
                                     {"llama_running": False}])
def test_monitor_submits_only_after_successful_handoff(tmp_path, monkeypatch, handoff):
    import koma.scheduler.monitor as monitor_mod

    store, cfg = make_series(tmp_path, PanelState.COLORIZED)
    save_queue(store.data_dir, {"jobs": [{"id": "one", "state": "pending"}]})
    worker = Mock()
    worker.is_online.return_value = True
    lane = (Mock(side_effect=handoff) if isinstance(handoff, Exception)
            else Mock(return_value=handoff))
    submit = Mock(side_effect=lambda cfg, worker, queue, job: job.update(state="running"))
    monkeypatch.setattr("koma.gpu.lanes.ensure_vision_lane", lane)
    monkeypatch.setattr(monitor_mod, "_submit_job", submit)
    monitor_mod.monitor_once(store.data_dir, cfg, store, worker=worker)
    expected = not isinstance(handoff, Exception) and not handoff["llama_running"]
    assert submit.call_count == int(expected)
    assert load_queue(store.data_dir)["jobs"][0]["state"] == ("running" if expected else "pending")
