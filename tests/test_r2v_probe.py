"""Probe recovery and local transport contracts, without model/network calls."""

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from koma.r2v_probe import (
    Client,
    NoRedirect,
    ProbeError,
    digest,
    execute,
    graph_hash,
    main,
    read_state,
    reconcile,
    save,
    select_output,
    submit,
    validate_schema,
    validate_video,
)

ROOT = Path(__file__).resolve().parents[1]
GRAPH = json.loads((ROOT / "config/workflows/minimax_h3_panel_r2v_turbo4.api.json").read_text())


class FakeClient:
    server = "http://127.0.0.1:8188"

    def __init__(self):
        self.queue = {"queue_running": [], "queue_pending": []}
        self.history = {}
        self.posts = []
        self.uploads = []
        self.downloads = 0
        self.post_error = None
        self.capture = None

    def json(self, route, payload=None):
        if route == "/queue":
            return self.queue
        if route.startswith("/history"):
            return self.history
        if route == "/prompt":
            self.posts.append(payload)
            if self.capture:
                self.capture()
            if self.post_error:
                raise self.post_error
            return {"prompt_id": "accepted-id", "node_errors": {}}
        raise AssertionError(route)

    def upload(self, panel, name):
        self.uploads.append(name)
        return name

    def download(self, output, target):
        self.downloads += 1
        target.write_bytes(b"downloaded video fixture")


def state_for(directory):
    panel = directory / "source.png"
    panel.write_bytes(b"source fixture")
    graph = copy.deepcopy(GRAPH)
    graph["137"]["inputs"]["image"] = "koma_probe_test.png"
    graph["92"]["inputs"]["filename_prefix"] = "koma/probe_test"
    state = {
        "schema_version": 1,
        "id": "test",
        "status": "prepared",
        "panel": str(panel),
        "panel_sha256": digest(panel),
        "graph": graph,
        "graph_sha256": graph_hash(graph),
        "server": FakeClient.server,
        "prompt_id": None,
    }
    save(directory / "run.json", state)
    return state


def entry(state, pid="accepted-id"):
    return [1, pid, state["graph"], {"koma_probe_id": state["id"]}, ["92"]]


class TestProbeRecovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.state = state_for(self.directory)
        self.client = FakeClient()

    def test_intent_is_durable_before_post(self):
        self.client.capture = lambda: self.assertEqual(
            read_state(self.directory)["status"], "submitting"
        )
        submit(self.client, self.state, self.directory)
        self.assertEqual(read_state(self.directory)["prompt_id"], "accepted-id")
        self.assertEqual(len(self.client.posts), 1)

    def test_lost_post_response_reconciles_without_second_submit(self):
        self.client.post_error = TimeoutError("response lost")
        self.client.capture = lambda: self.client.queue["queue_running"].append(entry(self.state))
        submit(self.client, self.state, self.directory)
        self.assertEqual(self.state["status"], "submission_uncertain")
        reconcile(self.client, self.state)
        self.assertEqual(self.state["prompt_id"], "accepted-id")
        self.assertEqual(len(self.client.posts), 1)

    def test_ambiguous_absent_or_multiple_matches_never_resubmit(self):
        self.state["status"] = "submitting"
        execute(self.client, self.state, self.directory, 0, 1)
        self.assertEqual(self.state["status"], "submission_uncertain")
        self.client.queue["queue_running"] = [entry(self.state, "a"), entry(self.state, "b")]
        execute(self.client, self.state, self.directory, 0, 1)
        self.assertEqual(self.state["status"], "submission_uncertain")
        self.assertEqual(self.client.posts, [])

    def test_same_marker_with_different_graph_is_not_adopted(self):
        item = copy.deepcopy(entry(self.state))
        item[2]["129"]["inputs"]["noise_seed"] = 99
        self.client.queue["queue_running"] = [item]
        reconcile(self.client, self.state)
        self.assertIsNone(self.state["prompt_id"])

    def test_accepted_resume_downloads_without_new_generation(self):
        self.state.update(status="accepted", prompt_id="accepted-id")
        self.client.history = {
            "accepted-id": {
                "prompt": entry(self.state),
                "status": {"completed": True, "status_str": "success"},
                "outputs": {
                    "92": {
                        "images": [{"filename": "video.mp4", "subfolder": "koma", "type": "output"}]
                    }
                },
            }
        }
        with (
            patch("koma.r2v_probe.validate_video", return_value={"streams": []}),
            patch("koma.r2v_probe.contact_sheet", return_value={"file": "contact-sheet.png"}),
        ):
            execute(self.client, self.state, self.directory, 0, 1)
            execute(self.client, self.state, self.directory, 0, 1)
        self.assertEqual(self.state["status"], "complete")
        self.assertEqual(self.client.downloads, 1)
        self.assertEqual(self.client.posts, [])
        self.assertTrue((self.directory / "ffprobe.json").exists())

    def test_download_failure_is_resumable_with_same_prompt(self):
        self.state.update(status="accepted", prompt_id="accepted-id")
        self.client.history = {
            "accepted-id": {
                "status": {"completed": True},
                "outputs": {"92": {"gifs": [{"filename": "v.mp4", "type": "output"}]}},
            }
        }
        with patch.object(self.client, "download", side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                execute(self.client, self.state, self.directory, 0, 1)
        self.assertEqual(self.state["prompt_id"], "accepted-id")
        self.assertEqual(self.client.posts, [])

    def test_busy_queue_and_changed_source_block_dispatch(self):
        self.client.queue["queue_pending"] = [[0, "unrelated"]]
        with self.assertRaisesRegex(ProbeError, "busy"):
            submit(self.client, self.state, self.directory)
        self.client.queue["queue_pending"] = []
        Path(self.state["panel"]).write_bytes(b"changed")
        with self.assertRaisesRegex(ProbeError, "changed"):
            submit(self.client, self.state, self.directory)
        self.assertEqual(self.client.posts, [])
        self.assertEqual(self.client.uploads, [])

    def test_validation_rejection_is_terminal_but_server_error_is_uncertain(self):
        for code, expected in ((400, "rejected"), (500, "submission_uncertain")):
            with self.subTest(code=code):
                client = FakeClient()
                client.post_error = HTTPError(
                    "local",
                    code,
                    "bad",
                    {},
                    io.BytesIO(b'{"error": {"type": "validation"}, "node_errors": {}}'),
                )
                submit(client, self.state, self.directory)
                self.assertEqual(self.state["status"], expected)

    def test_accepted_missing_history_is_held_not_failed_or_requeued(self):
        self.state.update(status="accepted", prompt_id="accepted-id")
        execute(self.client, self.state, self.directory, 0, 1)
        self.assertEqual(self.state["status"], "held")
        self.assertEqual(self.client.posts, [])
        self.client.queue["queue_running"] = [entry(self.state)]
        execute(self.client, self.state, self.directory, 0, 1)
        self.assertEqual(self.state["status"], "accepted")
        self.assertIsNone(self.state["error"])
        self.assertEqual(self.client.posts, [])

    def test_changed_saved_graph_is_rejected(self):
        self.state["graph"]["129"]["inputs"]["noise_seed"] = 9
        save(self.directory / "run.json", self.state)
        with self.assertRaisesRegex(ProbeError, "changed"):
            read_state(self.directory)

    def test_status_is_machine_readable_without_network_or_owner(self):
        with patch("sys.stdout", new_callable=io.StringIO) as stream:
            self.assertEqual(main(["status", "--run-dir", str(self.directory)]), 0)
        self.assertEqual(json.loads(stream.getvalue())["outcome"], "prepared")


class TestProbeValidation(unittest.TestCase):
    def test_local_transport_disables_proxies_and_redirects(self):
        with self.assertRaises(ValueError):
            Client("https://example.com")
        with patch("koma.r2v_probe.build_opener") as opener:
            Client("http://127.0.0.1:8188")
        proxy, redirect = opener.call_args.args
        self.assertEqual(proxy.proxies, {})
        with self.assertRaises(ProbeError):
            redirect.redirect_request(None, None, 302, "", {}, "https://example.com")
        self.assertIsInstance(redirect, NoRedirect)

    def test_installed_model_enum_and_link_type_checked(self):
        graph = {
            "1": {"class_type": "Loader", "inputs": {"name": "right"}},
            "2": {"class_type": "Use", "inputs": {"model": ["1", 0]}},
        }
        info = {
            "Loader": {"input": {"required": {"name": [["right"]]}}, "output": ["MODEL"]},
            "Use": {"input": {"required": {"model": ["MODEL"]}}, "output": []},
        }
        self.assertTrue(validate_schema(graph, info))
        graph["1"]["inputs"]["name"] = "wrong"
        with self.assertRaisesRegex(ProbeError, "not installed"):
            validate_schema(graph, info)
        graph["1"]["inputs"]["name"] = "right"
        info["Loader"]["output"] = ["IMAGE"]
        with self.assertRaisesRegex(ProbeError, "wrong output"):
            validate_schema(graph, info)

    def test_dynamic_reference_socket_requires_installed_group(self):
        graph = {
            "1": {"class_type": "Image", "inputs": {}},
            "2": {"class_type": "Ref", "inputs": {"ref_images.ref_image_0": ["1", 0]}},
        }
        info = {
            "Image": {"input": {}, "output": ["IMAGE"]},
            "Ref": {
                "input": {
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
                    }
                },
                "output": [],
            },
        }
        self.assertTrue(validate_schema(graph, info))
        info["Ref"]["input"] = {}
        with self.assertRaisesRegex(ProbeError, "unrecognized"):
            validate_schema(graph, info)

    def test_only_declared_save_video_output_is_selected(self):
        with self.assertRaises(ProbeError):
            select_output(
                {"outputs": {"other": {"videos": [{"filename": "wrong.mp4", "type": "output"}]}}}
            )
        with self.assertRaises(ProbeError):
            select_output(
                {
                    "outputs": {
                        "92": {
                            "videos": [
                                {"filename": "a.mp4", "type": "output"},
                                {"filename": "b.mp4", "type": "output"},
                            ]
                        }
                    }
                }
            )

    def test_video_mismatch_cannot_be_marked_complete(self):
        result = type(
            "Result",
            (),
            {
                "stdout": json.dumps(
                    {
                        "streams": [
                            {
                                "codec_type": "video",
                                "width": 864,
                                "height": 480,
                                "avg_frame_rate": "24/1",
                                "nb_read_frames": "123",
                            }
                        ]
                    }
                )
            },
        )()
        with patch("koma.r2v_probe.subprocess.run", return_value=result):
            with self.assertRaisesRegex(ProbeError, "frame count"):
                validate_video(Path("fixture.mp4"), GRAPH)


class TestProbeMediaIntegration(unittest.TestCase):
    def test_real_decode_and_contact_sheet(self):
        import shutil
        import subprocess

        from koma.r2v_probe import contact_sheet

        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("local ffmpeg and ffprobe required")
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            video = directory / "test.mp4"
            # Small, lossless synthetic media; no models or external files involved.
            graph = copy.deepcopy(GRAPH)
            graph["136"]["inputs"].update(width=64, height=32)
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
                    "mpeg4",
                    str(video),
                ],
                check=True,
                timeout=30,
            )
            info = validate_video(video, graph)
            self.assertEqual(info["streams"][0]["nb_read_frames"], "124")
            sheet = contact_sheet(video, directory, 124)
            self.assertEqual(sheet["frame_indices"], [0, 17, 35, 52, 70, 87, 105, 123])
            self.assertGreater((directory / sheet["file"]).stat().st_size, 0)


class TestProbeCLI(unittest.TestCase):
    def test_check_saves_capabilities_without_upload_or_submission(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "check"
            report = {"object_info": {}, "system_stats": {}}
            with (
                patch("koma.r2v_probe.prepare", return_value=(GRAPH, report)),
                patch("koma.r2v_probe.validate_schema") as validate,
                patch("koma.r2v_probe.Client") as client,
                patch("koma.r2v_probe.OwnerLock.acquire") as acquire,
                patch("sys.stdout", new_callable=io.StringIO) as output,
            ):
                code = main(
                    [
                        "check",
                        "--server",
                        "http://localhost:8188",
                        "--panel",
                        "panel.png",
                        "--run-dir",
                        str(directory),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["outcome"], "preflight_passed")
            self.assertTrue((directory / "preflight.json").exists())
            validate.assert_called_once()
            acquire.assert_not_called()
            client.return_value.upload.assert_not_called()
            self.assertFalse((directory / "run.json").exists())

    def test_owner_conflict_has_no_network_side_effect(self):
        from koma.scheduler.ownership import OwnerBusy

        with (
            patch("koma.r2v_probe.OwnerLock.acquire", side_effect=OwnerBusy("busy")),
            patch("koma.r2v_probe.Client") as client,
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            code = main(
                [
                    "run",
                    "--server",
                    "http://localhost:8188",
                    "--panel",
                    "panel.png",
                    "--run-dir",
                    "unused",
                ]
            )
        self.assertEqual(code, 5)
        self.assertEqual(json.loads(output.getvalue())["outcome"], "owner_busy")
        client.assert_not_called()
