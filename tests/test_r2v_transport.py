"""Local ComfyUI adapter contracts and durable worker integration; no GPU needed."""

from __future__ import annotations

import copy
import shutil
import subprocess

import pytest

from koma.adapters.reference_manifest import build_reference_manifest
from koma.r2v_compiler import reference_upload_plan
from koma.r2v_probe import ProbeError, digest
from koma.r2v_transport import ComfyR2VTransport
from koma.r2v_worker import R2VWorker
from tests.test_r2v_worker import _attempt, _create_work, _job
from tests.test_r2v_worker import env as worker_env  # noqa: F401

SERVER = "http://127.0.0.1:8188"
INFO = {
    "LoadImage": {"input": {"required": {"image": [["existing.png"]]}}, "output": ["IMAGE"]},
    "Ref": {
        "input": {
            "required": {
                "width": ["INT"],
                "height": ["INT"],
                "length": ["INT"],
                "prompt": ["STRING"],
            },
            "optional": {
                "ref_images": [
                    "AUTOGROW",
                    {
                        "template": {
                            "prefix": "ref_image_",
                            "max": 9,
                            "input": {"required": {"ref_image": ["IMAGE"]}},
                        }
                    },
                ]
            },
        },
        "output": ["VIDEO"],
    },
    "SaveVideo": {"input": {"required": {"video": ["VIDEO"]}}, "output": []},
}


class ComfyServer:
    def __init__(self, video):
        self.video = video
        self.posts = []
        self.uploads = []
        self.history = {}
        self.queue = {"queue_running": [], "queue_pending": []}
        self.lose_response = False

    def json(self, route, payload=None):
        if route == "/object_info":
            return INFO
        if route == "/queue":
            return self.queue
        if route.startswith("/history"):
            return self.history
        assert route == "/prompt"
        self.posts.append(copy.deepcopy(payload))
        self.history["prompt-1"] = {
            "prompt": [1, "prompt-1", payload["prompt"], payload["extra_data"], ["92"]],
            "status": {"completed": True, "status_str": "success"},
            "outputs": {"92": {"videos": [{"filename": "result.mp4", "type": "output"}]}},
        }
        if self.lose_response:
            raise TimeoutError("accepted but response lost")
        return {"prompt_id": "prompt-1"}

    def upload(self, path, name):
        assert path.is_file()
        self.uploads.append(name)

    def download(self, output, target):
        shutil.copyfile(self.video, target)


@pytest.fixture
def transport_env(worker_env, tmp_path):  # noqa: F811
    store, lock = worker_env
    panel = tmp_path / "panel.png"
    panel.write_bytes(b"immutable reference fixture")
    manifest = build_reference_manifest(
        panel_artifact_id="panel-artifact", panel_sha256=digest(panel), character_bindings=[]
    )
    uploads = reference_upload_plan(manifest, panel)
    graph = {
        "137": {"class_type": "LoadImage", "inputs": {"image": uploads[0]["upload_name"]}},
        "136": {
            "class_type": "Ref",
            "inputs": {
                "width": 64,
                "height": 32,
                "length": 124,
                "prompt": "<Picture 1> turns",
                "ref_images.ref_image_0": ["137", 0],
            },
        },
        "92": {"class_type": "SaveVideo", "inputs": {"video": ["136", 0]}},
    }
    job = _job(compiled_graph=graph, upload_plan=uploads)
    _create_work(store, job)
    with store.db.transaction() as conn:
        conn.execute(
            "INSERT INTO artifacts (id,series_id,scope_key,kind,mode,relative_path,sha256,"
            "size,input_fingerprint,validation_status,created_at) VALUES "
            "('panel-artifact','ser1','panel','panel','production','panel.png',?,1,'panel',"
            "'validated',datetime('now'))",
            (digest(panel),),
        )
        import json

        conn.execute(
            "UPDATE shot_ratio_specs SET reference_manifest=?,target_width=64,target_height=32",
            (json.dumps(manifest),),
        )
    video = tmp_path / "provider.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x32:r=24",
            "-frames:v",
            "124",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        timeout=30,
    )
    remote = ComfyServer(video)
    adapter = ComfyR2VTransport(store.db, SERVER, client=remote)
    worker = R2VWorker(
        store,
        lock,
        preparer=adapter.prepare,
        submitter=adapter.submit,
        poller=adapter.poll,
        fetcher=adapter.fetch,
        validator=adapter.validate,
        snapshotter=adapter.snapshot,
        server_identity=SERVER,
    )
    return store, worker, adapter, remote, job


def test_real_decode_and_promotion_with_reference_dependencies(transport_env):
    store, worker, _adapter, remote, job = transport_env
    result = worker.execute(job)
    assert result.status == "succeeded", result.detail
    assert len(remote.posts) == 1
    assert remote.posts[0]["extra_data"]["koma_request_identity"] == f"r2v:{result.attempt_id}"
    with store.db.transaction() as conn:
        artifact = conn.execute(
            "SELECT * FROM artifacts WHERE id=?", result.artifact_ids
        ).fetchone()
        dependencies = conn.execute(
            "SELECT input_artifact_id FROM artifact_dependencies WHERE artifact_id=?",
            result.artifact_ids,
        ).fetchall()
        assert [row[0] for row in dependencies] == ["panel-artifact"]
        assert conn.execute("SELECT count(*) FROM shot_candidates").fetchone()[0] == 1
    assert (store.db.data_dir / artifact["relative_path"]).is_file()
    assert artifact["validation_status"] == "validated"


def test_lost_response_reconciles_same_attempt_and_one_post(transport_env):
    store, worker, _adapter, remote, job = transport_env
    remote.lose_response = True
    first = worker.execute(job)
    assert _attempt(store, first.attempt_id)["state"] == "submission_uncertain"
    second = worker.execute(job)
    assert second.status == "succeeded", second.detail
    assert first.attempt_id == second.attempt_id
    assert len(remote.posts) == 1


def test_busy_queue_stays_prepared_and_can_resume_without_reconciliation(transport_env):
    store, worker, _adapter, remote, job = transport_env
    remote.queue["queue_running"] = [[0, "external", {}, {}]]
    first = worker.execute(job)
    assert first.status == "held"
    assert _attempt(store, first.attempt_id)["state"] == "prepared"
    assert not remote.posts and not remote.uploads
    remote.queue["queue_running"] = []
    second = worker.execute(job)
    assert second.status == "succeeded", second.detail
    assert first.attempt_id == second.attempt_id


@pytest.mark.parametrize("change", ["file", "path", "name", "socket", "canvas"])
def test_reference_mismatches_never_reach_post(transport_env, change):
    store, _worker, adapter, remote, job = transport_env
    graph = copy.deepcopy(job.desired_spec["compiled_graph"])
    uploads = copy.deepcopy(job.desired_spec["upload_plan"])
    if change == "file":
        (store.db.data_dir / "panel.png").write_bytes(b"changed")
    elif change == "path":
        with store.db.transaction() as conn:
            conn.execute("UPDATE artifacts SET relative_path='../escape.png'")
    elif change == "name":
        uploads[0]["upload_name"] = "other.png"
    elif change == "socket":
        graph["136"]["inputs"]["ref_images.ref_image_0"] = ["92", 0]
    else:
        graph["136"]["inputs"]["width"] = 128
    with pytest.raises(ProbeError):
        adapter.prepare(graph, uploads, {**job.desired_spec, "ratio": job.ratio})
    assert not remote.posts and not remote.uploads


def test_output_identity_and_graph_are_checked(transport_env):
    store, worker, adapter, remote, job = transport_env
    remote.lose_response = True
    first = worker.execute(job)
    record = remote.history["prompt-1"]
    record["prompt"][3]["koma_request_identity"] = "another-attempt"
    with pytest.raises(ProbeError, match="does not belong"):
        adapter.fetch("prompt-1", store.db.data_dir / "attempts" / first.attempt_id)
    assert worker.execute(job).status == "held"
    assert len(remote.posts) == 1


def test_absent_history_never_means_failure_or_resubmit(transport_env):
    _, _, adapter, remote, _ = transport_env
    assert adapter.poll("missing") == "running"
    assert adapter.snapshot() == {}
    assert not remote.posts


def test_public_server_rejected_before_any_request(worker_env):  # noqa: F811
    store, _ = worker_env
    with pytest.raises(ValueError, match="local"):
        ComfyR2VTransport(store.db, "https://example.com")


def test_upload_failure_leaves_prepared_attempt_retryable(transport_env, monkeypatch):
    store, worker, _adapter, remote, job = transport_env
    original = remote.upload

    def unavailable(path, name):
        raise ConnectionError("upload unavailable")

    monkeypatch.setattr(remote, "upload", unavailable)
    first = worker.execute(job)
    assert _attempt(store, first.attempt_id)["state"] == "prepared"
    assert not remote.posts
    monkeypatch.setattr(remote, "upload", original)
    second = worker.execute(job)
    assert second.status == "succeeded", second.detail
    assert second.attempt_id == first.attempt_id
    assert len(remote.posts) == 1
