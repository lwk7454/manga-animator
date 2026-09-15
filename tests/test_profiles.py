"""Durable feature tests; no live model services."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from koma.adapters.registry import AdapterError, resolve_profile, verify_component
from koma.config import Settings


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


CONFIG = Path(__file__).resolve().parents[1] / "config"


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
