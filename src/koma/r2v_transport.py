"""Local ComfyUI transport for frozen direct-R2V work.

Reuse the probe's no-proxy/no-redirect client, schema checks and decoded-video
validation. The worker owns durable intent, submission uncertainty and locking;
this adapter never retries POST /prompt or changes a frozen graph.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from .adapters.reference_manifest import (
    compile_prompt_with_tags,
    validate_manifest_against_capacity,
)
from .r2v_probe import (
    Client,
    ProbeError,
    digest,
    graph_hash,
    queue_entries,
    select_output,
    validate_schema,
    validate_video,
)
from .storage.shot_store import ShotStore
from .util import canonical_hash


class ComfyR2VTransport:
    def __init__(self, db, server: str, *, client=None):
        self.db = db
        # Validate locality even when the HTTP client is injected in tests.
        checked = Client(server)
        self.client = client if client is not None else checked
        self.server_identity = checked.server
        self.root = db.data_dir.resolve()
        self._prepared_hash = None

    def _path(self, relative: str) -> Path:
        value = Path(relative)
        path = (self.root / value).resolve()
        if value.is_absolute() or not path.is_relative_to(self.root) or not path.is_file():
            raise ProbeError("artifact must be an existing file inside the data root")
        return path

    def prepare(self, graph: dict, uploads: list, spec: dict) -> None:
        """Verify exact bindings and upload before the uncertain POST boundary."""
        self._prepared_hash = None
        shots = ShotStore(self.db)
        ready = shots.require_production_ready(spec["shot_revision_id"], spec["ratio"])
        if ready["route"] != "direct_r2v":
            raise ProbeError("only direct_r2v transport is implemented")
        ratio = shots.get_ratio_spec(spec["shot_revision_id"], spec["ratio"])
        manifest = ratio["reference_manifest"]
        validate_manifest_against_capacity(manifest)
        inputs = graph["136"]["inputs"]
        compile_prompt_with_tags(inputs["prompt"], manifest)
        if (inputs["width"], inputs["height"]) != (ratio["target_width"], ratio["target_height"]):
            raise ProbeError("graph canvas differs from frozen ratio specification")
        expected_slots = {f"ref_images.{m['slot_key']}" for m in manifest}
        if {k for k in inputs if k.startswith("ref_images.")} != expected_slots:
            raise ProbeError("graph reference sockets differ from frozen manifest")
        if len(uploads) != len(manifest):
            raise ProbeError("upload plan differs from frozen reference manifest")
        by_id = {u["artifact_id"]: u for u in uploads}
        if len(by_id) != len(uploads):
            raise ProbeError("duplicate upload artifact")
        with self.db.transaction() as conn:
            series = conn.execute(
                "SELECT series_id FROM shot_plan_revisions WHERE id=?",
                (spec["shot_revision_id"],),
            ).fetchone()["series_id"]
            artifacts = {
                m["artifact_id"]: conn.execute(
                    "SELECT * FROM artifacts WHERE id=?", (m["artifact_id"],)
                ).fetchone()
                for m in manifest
            }
        files = []
        for m in manifest:
            upload = by_id.get(m["artifact_id"], {})
            name = f"koma_{canonical_hash([m['artifact_id'], m['sha256']])}.png"
            artifact = artifacts[m["artifact_id"]]
            if (
                upload.get("sha256") != m["sha256"]
                or upload.get("upload_name") != name
                or artifact is None
                or artifact["series_id"] != series
                or artifact["sha256"] != m["sha256"]
            ):
                raise ProbeError("upload artifact/hash/name differs from frozen reference")
            link = inputs[f"ref_images.{m['slot_key']}"]
            node = graph.get(link[0], {})
            if (
                link[1] != 0
                or node.get("class_type") != "LoadImage"
                or node.get("inputs", {}).get("image") != name
            ):
                raise ProbeError("graph reference is not bound to its frozen upload")
            # Resolve all files from artifact records, never caller-supplied local_path.
            path = self._path(artifact["relative_path"])
            if digest(path) != m["sha256"]:
                raise ProbeError("reference file changed since planning")
            files.append((path, name, m["sha256"]))
        load_names = [
            n["inputs"]["image"] for n in graph.values() if n["class_type"] == "LoadImage"
        ]
        if sorted(load_names) != sorted(name for _, name, _ in files):
            raise ProbeError("graph includes undeclared image inputs")
        validate_schema(graph, self.client.json("/object_info"))
        if queue_entries(self.client.json("/queue")):
            raise ProbeError("ComfyUI queue is busy; resume prepared work after it drains")
        for path, name, sha in files:
            self.client.upload(path, name)
            if digest(path) != sha:
                raise ProbeError("reference changed during upload; no prompt submitted")
        self._prepared_hash = graph_hash(graph)

    def submit(self, graph: dict, uploads: list, context: dict) -> str:
        if self._prepared_hash != graph_hash(graph):
            raise ProbeError("graph was not prepared by this transport")
        if context.get("server_identity") != self.server_identity or not context.get(
            "request_identity"
        ):
            raise ProbeError("submission identity is missing or points to another server")
        self._prepared_hash = None  # One POST only, including when the response is lost.
        result = self.client.json(
            "/prompt",
            {
                "prompt": graph,
                "client_id": context["request_identity"],
                "extra_data": {
                    "koma_request_identity": context["request_identity"],
                    "koma_server_identity": self.server_identity,
                },
            },
        )
        if not isinstance(result.get("prompt_id"), str) or result.get("node_errors"):
            raise ProbeError("invalid ComfyUI acceptance response; reconcile before retry")
        return result["prompt_id"]

    def _history(self, prompt_id: str) -> dict:
        history = self.client.json("/history/" + quote(prompt_id, safe=""))
        return history.get(prompt_id, {})

    def poll(self, prompt_id: str) -> str:
        record = self._history(prompt_id)
        status = record.get("status", {})
        if status.get("status_str") == "error":
            return "failed"
        if status.get("completed") is True and status.get("status_str") == "success":
            select_output(record)  # Malformed success stays held for inspection.
            return "generated"
        # Missing history is not evidence of rejection, failure or permission to resubmit.
        return "running"

    def snapshot(self) -> dict:
        queue = self.client.json("/queue")
        history = self.client.json("/history?max_items=1000")
        entries = queue_entries(queue) + [h.get("prompt") for h in history.values()]
        result = {}
        conflicts = set()
        for entry in entries:
            normalized = self._entry(entry)
            if normalized is None:
                continue
            pid, record = normalized
            if pid in result and result[pid] != record:
                conflicts.add(pid)
            result[pid] = record
        return {pid: record for pid, record in result.items() if pid not in conflicts}

    @staticmethod
    def _entry(entry):
        if not isinstance(entry, list) or len(entry) < 4:
            return None
        if not isinstance(entry[1], str) or not isinstance(entry[3], dict):
            return None
        return entry[1], {
            "graph": entry[2],
            "request_identity": entry[3].get("koma_request_identity"),
            "server_identity": entry[3].get("koma_server_identity"),
        }

    def fetch(self, prompt_id: str, target_dir: Path) -> list[dict]:
        directory = target_dir.resolve()
        if not directory.is_relative_to(self.root / "attempts"):
            raise ProbeError("download target must be inside the attempts directory")
        record = self._history(prompt_id)
        status = record.get("status", {})
        if status.get("completed") is not True or status.get("status_str") != "success":
            raise ProbeError("remote generation is not successfully completed")
        entry = self._entry(record.get("prompt"))
        if (
            entry is None
            or entry[0] != prompt_id
            or entry[1]["server_identity"] != self.server_identity
            or entry[1]["request_identity"] != f"r2v:{directory.name}"
        ):
            raise ProbeError("remote output does not belong to this attempt/server")
        output = select_output(record)
        # Remote names are only /view parameters, never local paths.
        suffix = Path(output["filename"]).suffix.lower()
        target = directory / ("motion" + suffix)
        directory.mkdir(parents=True, exist_ok=True)
        self.client.download(output, target)
        return [
            {
                "kind": "motion",
                "relative_path": target.relative_to(self.root).as_posix(),
                "sha256": digest(target),
                "size": target.stat().st_size,
                "result_metadata": {
                    "remote_prompt_id": prompt_id,
                    "server_identity": self.server_identity,
                    "compiled_workflow_hash": graph_hash(entry[1]["graph"]),
                    "request_identity": entry[1]["request_identity"],
                    "visual_quality_qualified": False,
                },
            }
        ]

    def validate(self, artifacts: list, spec: dict) -> list[dict]:
        if len(artifacts) != 1:
            raise ProbeError("expected exactly one motion artifact")
        artifact = artifacts[0]
        if artifact["result_metadata"]["compiled_workflow_hash"] != graph_hash(
            spec["compiled_graph"]
        ):
            raise ProbeError("remote graph differs from frozen work")
        path = self._path(artifact["relative_path"])
        if digest(path) != artifact["sha256"]:
            raise ProbeError("download changed before validation")
        media = validate_video(path, spec["compiled_graph"])
        return [
            {
                **artifact,
                "media_metadata": media,
                "dependencies": [
                    {"input_artifact_id": u["artifact_id"], "semantic_role": u["role"]}
                    for u in spec["upload_plan"]
                ],
            }
        ]


def build_worker(db, server: str):
    from .r2v_worker import R2VWorker
    from .scheduler.ownership import OwnerLock
    from .storage.domain_store import KomaStore

    adapter = ComfyR2VTransport(db, server)
    return R2VWorker(
        KomaStore(db),
        OwnerLock(),
        preparer=adapter.prepare,
        submitter=adapter.submit,
        poller=adapter.poll,
        fetcher=adapter.fetch,
        validator=adapter.validate,
        snapshotter=adapter.snapshot,
        server_identity=adapter.server_identity,
    )
