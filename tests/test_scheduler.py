"""Durable feature tests; no live model services."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from koma.cli import app
from koma.models.domain import PanelState
from koma.scheduler.queue import load_queue, save_queue, set_paused
from tests.helpers import make_series


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
