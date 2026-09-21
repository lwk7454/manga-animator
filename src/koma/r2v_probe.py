"""Agent-facing, local-only R2V qualification runner (not the production queue)."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
import uuid
from fractions import Fraction
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .network import require_local_endpoint
from .scheduler.ownership import OwnerBusy, OwnerLock

ROOT = Path(__file__).resolve().parents[2]
MODELS = {
    "unet": ("127", "unet_name"),
    "clip": ("128", "clip_name"),
    "vae": ("119", "vae_name"),
    "lora": ("145", "lora_name"),
}


class ProbeError(RuntimeError):
    """An actionable configuration, protocol or validation failure."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProbeError("ComfyUI redirects are disabled")


class Client:
    def __init__(self, server, timeout=30):
        self.server = require_local_endpoint(server)
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, route, data=None, headers=None):
        req = Request(self.server + route, data=data, headers=headers or {})
        return self.opener.open(req, timeout=self.timeout)

    def json(self, route, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        headers = {} if data is None else {"Content-Type": "application/json"}
        with self.request(route, data, headers) as response:
            return json.load(response)

    def upload(self, panel, name):
        boundary = uuid.uuid4().hex
        # Names are generated locally, not taken from the input path.
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
            f'filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'
        ).encode()
        body += panel.read_bytes()
        for key, value in (("type", "input"), ("overwrite", "true")):
            body += (
                f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}'
            ).encode()
        body += f"\r\n--{boundary}--\r\n".encode()
        with self.request(
            "/upload/image", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        ) as response:
            result = json.load(response)
        if result.get("name") != name or result.get("subfolder", ""):
            raise ProbeError("unexpected upload location; inspect the saved run")
        return name

    def download(self, output, target):
        if output.get("type") != "output":
            raise ProbeError("expected a persisted ComfyUI output file")
        query = {k: output.get(k, "") for k in ("filename", "subfolder", "type")}
        part = target.with_suffix(".part")
        with self.request("/view?" + urlencode(query)) as response, part.open("wb") as stream:
            shutil.copyfileobj(response, stream, length=1024 * 1024)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(part, target)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _canonicalize_numbers(obj):
    """ComfyUI coerces integer widget values (e.g. CreateVideo fps: 24) to
    floats when persisting the executed graph. Canonicalize integral floats
    to ints so a frozen-work hash matches the remote record."""
    if isinstance(obj, dict):
        return {k: _canonicalize_numbers(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_canonicalize_numbers(v) for v in obj]
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)
    return obj


def graph_hash(graph):
    canonical = _canonicalize_numbers(graph)
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def save(path, payload):
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read_state(directory):
    state = json.loads((directory / "run.json").read_text())
    if state.get("schema_version") != 1:
        raise ProbeError("unsupported probe run format")
    require_local_endpoint(state["server"])
    if graph_hash(state["graph"]) != state["graph_sha256"]:
        raise ProbeError("saved graph changed; do not edit a submitted run")
    return state


def enums(spec):
    if isinstance(spec[0], list):
        return spec[0]
    if len(spec) > 1 and isinstance(spec[1], dict):
        options = spec[1].get("options")
        if isinstance(options, list) and options and isinstance(options[0], dict):
            # COMFY_DYNAMICCOMBO_V3: option dicts with a "key" are valid values.
            return [o["key"] for o in options if isinstance(o, dict) and "key" in o]
        return options
    return None


def validate_schema(graph, info):
    """Validate installed node presence, links, required inputs and model enums."""
    for node in graph.values():
        kind, inputs = node["class_type"], node["inputs"]
        schema = info.get(kind)
        if not schema:
            raise ProbeError(f"installed ComfyUI lacks node {kind}")
        groups = schema.get("input", {})
        declared = {**groups.get("required", {}), **groups.get("optional", {})}
        for name in groups.get("required", {}):
            if name not in inputs and not any(k.startswith(name + ".") for k in inputs):
                raise ProbeError(f"{kind}: missing required input {name}")
        for name, value in inputs.items():
            spec = declared.get(name)
            if spec is None:
                # V3 autogrow schemas can expose a group instead of flattened sockets.
                group = declared.get("ref_images", [None, {}])
                template = group[1].get("template", {}) if len(group) > 1 else {}
                prefix = "ref_images.ref_image_"
                index = name.removeprefix(prefix)
                if (
                    name.startswith(prefix)
                    and index.isdecimal()
                    and str(int(index)) == index
                    and template.get("prefix") == "ref_image_"
                    and int(index) < template.get("max", 0)
                ):
                    fields = template.get("input", {})
                    choices = {**fields.get("required", {}), **fields.get("optional", {})}
                    spec = next(iter(choices.values()), None)
                if spec is None:
                    raise ProbeError(f"{kind}: unrecognized installed input {name}")
            if isinstance(value, list):
                upstream, slot = value
                if upstream not in graph:
                    raise ProbeError(f"{kind}: dangling link {name}")
                types = info[graph[upstream]["class_type"]].get("output", [])
                if slot < 0 or slot >= len(types) or types[slot] != spec[0]:
                    raise ProbeError(f"{kind}: wrong output type for {name}")
            elif name != "image":  # LoadImage's enum cannot contain our upload yet.
                options = enums(spec)
                if options is not None and value not in options:
                    raise ProbeError(f"{kind}.{name}: {value!r} not installed/supported")
                limits = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
                if isinstance(value, (int, float)):
                    if not limits.get("min", -math.inf) <= value <= limits.get("max", math.inf):
                        raise ProbeError(f"{kind}.{name}: outside installed limits")
    return True


def prepare(args, client):
    for binary in ("ffprobe", "ffmpeg"):
        if not shutil.which(binary):
            raise ProbeError(f"{binary} must be installed before generation")
    from PIL import Image

    panel = args.panel.resolve()
    if not panel.is_file() or panel.stat().st_size > 64 * 1024 * 1024:
        raise ProbeError("panel must be an image file under 64 MiB")
    with Image.open(panel) as image:
        source_size = image.size
        fmt = image.format
        image.verify()
    if fmt not in {"PNG", "JPEG", "WEBP"}:
        raise ProbeError("use PNG, JPEG or WEBP for the source panel")
    graph = json.loads(
        (ROOT / "config/workflows" / f"minimax_h3_panel_r2v_{args.variant}.api.json").read_text()
    )
    if args.models:
        overrides = json.loads(args.models.read_text())
        if not isinstance(overrides, dict) or set(overrides) - set(MODELS):
            raise ProbeError("models JSON accepts only unet, clip, vae and lora keys")
        for role, name in overrides.items():
            node, field = MODELS[role]
            if node not in graph or not isinstance(name, str):
                raise ProbeError(f"invalid model override: {role}")
            # Permit installed subdirectories, not silently switching model families.
            expected = graph[node]["inputs"][field]
            if name.replace("\\", "/").split("/")[-1] != expected:
                raise ProbeError(f"{role}: keep the workflow's model basename {expected}")
            graph[node]["inputs"][field] = name
    width, height = args.width, args.height
    if (width is None) != (height is None):
        raise ProbeError("supply both --width and --height")
    if width is None:
        width, height = (864, 480) if source_size[0] >= source_size[1] else (480, 864)
    if min(width, height) < 32 or width % 32 or height % 32:
        raise ProbeError("width and height must be positive multiples of 32")
    if args.frames < 124 or args.frames > 362 or args.frames % 17 != 5:
        raise ProbeError("use 17n+5 frames within the initial 124..362 test range")
    prompt = args.prompt_file.read_text(encoding="utf-8").strip()
    if not prompt or "<Picture 1>" not in prompt:
        raise ProbeError("prompt must describe the panel and include <Picture 1>")
    graph["136"]["inputs"].update(prompt=prompt, width=width, height=height, length=args.frames)
    graph["129"]["inputs"]["noise_seed"] = args.seed
    info = client.json("/object_info")
    selected = {
        n["class_type"]: info[n["class_type"]] for n in graph.values() if n["class_type"] in info
    }
    return graph, {
        "schema_version": 1,
        "server": client.server,
        "variant": args.variant,
        "panel": str(panel),
        "panel_sha256": digest(panel),
        "source_size": source_size,
        "image_extension": fmt.lower().replace("jpeg", "jpg"),
        "object_info": selected,
        "system_stats": client.json("/system_stats"),
    }


def queue_entries(queue):
    return queue.get("queue_running", []) + queue.get("queue_pending", [])


def matches(entry, state):
    if not isinstance(entry, list) or len(entry) < 4:
        return False
    graph, extra = entry[2], entry[3]
    return (
        isinstance(extra, dict)
        and extra.get("koma_probe_id") == state["id"]
        and graph == state["graph"]
    )


def reconcile(client, state):
    queue = client.json("/queue")
    history = client.json("/history?max_items=1000")
    candidates = {str(e[1]) for e in queue_entries(queue) if matches(e, state)}
    candidates.update(str(pid) for pid, h in history.items() if matches(h.get("prompt"), state))
    if len(candidates) == 1:
        state.update(prompt_id=candidates.pop(), status="accepted")
    else:
        state.update(
            status="submission_uncertain",
            error=(
                "No unique matching request in queue/recent history. Do not resubmit. "
                "History absence does not prove rejection; retain this run for review."
            ),
        )


def submit(client, state, directory):
    if queue_entries(client.json("/queue")):
        raise ProbeError("ComfyUI queue is busy; resume this prepared run after it drains")
    panel = Path(state["panel"])
    if digest(panel) != state["panel_sha256"]:
        raise ProbeError("source panel changed since preparation")
    client.upload(panel, state["graph"]["137"]["inputs"]["image"])
    state["status"] = "submitting"
    state["submitted_at_unix"] = time.time()
    save(directory / "run.json", state)  # durable BEFORE the potentially ambiguous POST
    try:
        result = client.json(
            "/prompt",
            {
                "prompt": state["graph"],
                "client_id": state["id"],
                "extra_data": {"koma_probe_id": state["id"]},
            },
        )
        if not isinstance(result.get("prompt_id"), str) or result.get("node_errors"):
            raise ProbeError(f"unexpected submission response: {result}")
        state.update(status="accepted", prompt_id=result["prompt_id"])
    except HTTPError as exc:
        # Only ComfyUI's explicit graph-validation response proves nonacceptance.
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 400:
            try:
                rejected = json.loads(body)
            except ValueError:
                rejected = {}
            if rejected.get("error") and "node_errors" in rejected:
                state.update(status="rejected", error=rejected)
            else:
                state.update(status="submission_uncertain", error=body[:4000])
        else:
            state.update(status="submission_uncertain", error=body[:4000])
    except Exception as exc:
        state.update(status="submission_uncertain", error=str(exc))
    save(directory / "run.json", state)


def select_output(history):
    outputs = history.get("outputs", {}).get("92", {})
    candidates = []
    for key in ("images", "gifs", "videos"):
        for item in outputs.get(key, []):
            if (
                isinstance(item, dict)
                and item.get("type") == "output"
                and Path(item.get("filename", "")).suffix.lower() in {".mp4", ".webm", ".mkv"}
            ):
                if item not in candidates:
                    candidates.append(item)
    if len(candidates) != 1:
        raise ProbeError("SaveVideo node 92 did not return exactly one video; inspect history.json")
    return candidates[0]


def validate_video(path, graph):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    info = json.loads(result.stdout)
    videos = [s for s in info["streams"] if s["codec_type"] == "video"]
    expected = graph["136"]["inputs"]
    if len(videos) != 1 or any(s["codec_type"] == "audio" for s in info["streams"]):
        raise ProbeError("expected one video stream and no audio")
    video = videos[0]
    if (
        (video["width"], video["height"]) != (expected["width"], expected["height"])
        or int(video.get("nb_read_frames", 0)) != expected["length"]
        or Fraction(video["avg_frame_rate"]) != 24
    ):
        raise ProbeError("decoded dimensions/frame count/fps do not match the submitted graph")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"],
        capture_output=True,
        check=True,
        timeout=300,
    )
    return info


def contact_sheet(video, directory, frames):
    indices = [i * (frames - 1) // 7 for i in range(8)]
    select = "+".join(f"eq(n\\,{index})" for index in indices)
    target = directory / "contact-sheet.png"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(video),
            "-vf",
            f"select='{select}',scale=320:-2,tile=4x2",
            "-frames:v",
            "1",
            str(target),
        ],
        capture_output=True,
        check=True,
        timeout=300,
    )
    return {"file": target.name, "frame_indices": indices}


def execute(client, state, directory, wait_seconds, poll_seconds):
    if state["status"] == "complete":
        path = directory / state["output_file"]
        if not path.is_file() or digest(path) != state["output_sha256"]:
            raise ProbeError("completed output missing/modified; retain run evidence for review")
        return
    if state["status"] == "rejected" or state["status"] == "failed":
        return
    if state["status"] == "prepared":
        submit(client, state, directory)
    if state["status"] in {"submitting", "submission_uncertain"}:
        reconcile(client, state)
        save(directory / "run.json", state)
    if not state.get("prompt_id"):
        return
    deadline = time.monotonic() + wait_seconds
    while True:
        pid = state["prompt_id"]
        records = client.json("/history/" + pid)
        history = records.get(pid)
        if history:
            save(directory / "history.json", history)
            status = history.get("status", {})
            if status.get("status_str") == "error":
                state.update(status="failed", error=status)
                save(directory / "run.json", state)
                return
            if status.get("completed"):
                output = select_output(history)
                target = directory / ("video" + Path(output["filename"]).suffix.lower())
                if not target.exists():
                    client.download(output, target)
                try:
                    info = validate_video(target, state["graph"])
                except Exception:
                    # Retry the download on resume, never create another generation.
                    target.unlink(missing_ok=True)
                    raise
                save(directory / "ffprobe.json", info)
                try:
                    state["contact_sheet"] = contact_sheet(
                        target, directory, state["graph"]["136"]["inputs"]["length"]
                    )
                except Exception as exc:
                    state["contact_sheet_error"] = str(exc)
                state.update(
                    status="complete",
                    completed_at_unix=time.time(),
                    output_file=target.name,
                    output_sha256=digest(target),
                    error=None,
                )
                save(directory / "run.json", state)
                return
        elif not any(str(e[1]) == pid for e in queue_entries(client.json("/queue"))):
            state.update(
                status="held", error="Accepted prompt absent from queue/history; review required"
            )
            save(directory / "run.json", state)
            return
        if state["status"] == "held":
            state.update(status="accepted", error=None)
            save(directory / "run.json", state)
        if time.monotonic() >= deadline:
            return  # accepted work continues remotely; resume polls the same ID
        time.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    for name in ("check", "run"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--server", required=True)
        cmd.add_argument("--panel", required=True, type=Path)
        cmd.add_argument(
            "--prompt-file", type=Path, default=ROOT / "config/prompts/manga_r2v_probe.txt"
        )
        cmd.add_argument("--variant", choices=("turbo4", "baseline20"), default="turbo4")
        cmd.add_argument("--models", type=Path, help="JSON of installed subdirectory paths")
        cmd.add_argument("--width", type=int)
        cmd.add_argument("--height", type=int)
        cmd.add_argument("--frames", type=int, default=124)
        cmd.add_argument("--seed", type=int, default=42)
        cmd.add_argument("--run-dir", type=Path, required=True)
    for name in ("resume", "status"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--run-dir", type=Path, required=True)
    for cmd in commands.choices.values():
        cmd.add_argument("--request-timeout", type=float, default=30)
        cmd.add_argument("--wait-seconds", type=float, default=900)
        cmd.add_argument("--poll-seconds", type=float, default=5)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    directory = args.run_dir.resolve()
    state = None
    lock, owner = OwnerLock(), None
    try:
        if any(
            not math.isfinite(v) or v <= 0
            for v in (args.request_timeout, args.wait_seconds, args.poll_seconds)
        ):
            raise ProbeError("timeouts and poll interval must be finite positive numbers")
        if args.command == "status":
            state = read_state(directory)
        else:
            if args.command != "check":
                owner = lock.acquire("r2v-probe " + args.command, directory)
            if args.command in {"check", "run"}:
                if directory.exists():
                    raise ProbeError("run directory exists; use resume or choose a new directory")
                client = Client(args.server, args.request_timeout)
                graph, report = prepare(args, client)
                directory.mkdir(parents=True, exist_ok=False)
                save(directory / "preflight.json", report)
                validate_schema(graph, report["object_info"])
                if args.command == "check":
                    print(
                        json.dumps(
                            {
                                "outcome": "preflight_passed",
                                "run_dir": str(directory),
                                "qualified": False,
                            }
                        )
                    )
                    return 0
                probe_id = uuid.uuid4().hex
                graph["137"]["inputs"]["image"] = (
                    f"koma_probe_{probe_id}.{report['image_extension']}"
                )
                graph["92"]["inputs"]["filename_prefix"] = "koma/probe_" + probe_id
                state = {k: report[k] for k in ("server", "variant", "panel", "panel_sha256")}
                state.update(
                    schema_version=1,
                    id=probe_id,
                    created_at_unix=time.time(),
                    status="prepared",
                    graph=graph,
                    graph_sha256=graph_hash(graph),
                    prompt_id=None,
                )
                save(directory / "run.json", state)
                save(directory / "prompt.json", graph)
            else:
                state = read_state(directory)
                client = Client(state["server"], args.request_timeout)
            execute(client, state, directory, args.wait_seconds, args.poll_seconds)
        print(
            json.dumps(
                {
                    "outcome": state["status"],
                    "run_dir": str(directory),
                    "prompt_id": state.get("prompt_id"),
                    "error": state.get("error"),
                    "output": state.get("output_file"),
                    "qualified": False,
                }
            )
        )
        return 0 if state["status"] == "complete" or args.command == "status" else 4
    except OwnerBusy as exc:
        print(json.dumps({"outcome": "owner_busy", "error": str(exc)}))
        return 5
    except KeyboardInterrupt:
        print(
            json.dumps(
                {
                    "outcome": "interrupted",
                    "run_dir": str(directory),
                    "message": "Remote work may continue. Resume this directory; do not resubmit.",
                }
            )
        )
        return 130
    except Exception as exc:
        if state is not None:
            state["error"] = str(exc)
            save(directory / "run.json", state)
        print(json.dumps({"outcome": "error", "error": str(exc), "run_dir": str(directory)}))
        return 4 if state else 2
    finally:
        if owner is not None:
            lock.release(owner)


if __name__ == "__main__":
    sys.exit(main())
