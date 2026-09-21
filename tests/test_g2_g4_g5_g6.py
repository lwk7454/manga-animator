from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from koma.application.chapter_assembly import offset_subtitles
from koma.application.composition import (
    DEFAULT_COMPOSITION_ROLE,
    ROLE_I2V_FIRST_FRAME,
    ROLE_R2V_REF,
    assert_exclusive_roles,
    composition_entry,
    select_route,
)
from koma.delivery import center_crop_box, center_crop_image
from koma.i2v_compiler import compile_i2v_graph, require_first_frame_role
from koma.scenes import order_source_panels, pick_primary_panel
from koma.storage.errors import InvalidTransition


def test_default_composition_is_i2v():
    assert select_route(use_composition=True)["adapter_role"] == ROLE_I2V_FIRST_FRAME
    assert DEFAULT_COMPOSITION_ROLE == ROLE_I2V_FIRST_FRAME


def test_composition_roles_exclusive():
    manifest = [
        {"role": "story_composition"},
        composition_entry(artifact_id="a", sha256="s", role=ROLE_R2V_REF, ratio="16:9"),
        composition_entry(artifact_id="b", sha256="t", role=ROLE_I2V_FIRST_FRAME, ratio="9:16"),
    ]
    with pytest.raises(InvalidTransition, match="never relabel"):
        assert_exclusive_roles(manifest)


def test_i2v_rejects_relabel():
    with pytest.raises(InvalidTransition, match="never relabel"):
        require_first_frame_role(
            [
                {"role": "story_composition"},
                {"role": ROLE_I2V_FIRST_FRAME, "artifact_id": "a"},
                {"role": ROLE_R2V_REF, "artifact_id": "b"},
            ]
        )


def test_i2v_graph_first_frame_only():
    graph = compile_i2v_graph(
        first_frame_upload="koma_comp.png",
        prompt="Animate the colored first frame with breathing.",
        width=480,
        height=864,
        frames=81,
        seed=7,
    )
    assert graph["6"]["inputs"]["image"] == "koma_comp.png"
    assert "ref_images" not in graph["7"]["inputs"]
    assert "audio_vae" not in graph["7"]["inputs"]


def test_center_crop_3x4_to_9x16():
    # 480x640 is 3:4; target 9:16 crops width to 640*(9/16)=356 centered
    x, y, w, h = center_crop_box(480, 640, 480, 864)
    assert y == 0 and h == 640
    assert w == 356 and x == (480 - 356) // 2


def test_center_crop_writes(tmp_path: Path):
    import cv2

    src = tmp_path / "recolor.png"
    image = np.zeros((640, 480, 3), dtype=np.uint8)
    image[:] = (12, 80, 200)
    cv2.imwrite(str(src), image)
    dst = tmp_path / "nine.png"
    center_crop_image(src, dst, ratio="9:16")
    out = cv2.imread(str(dst))
    assert out.shape[1] == 480 and out.shape[0] == 864


def test_primary_panel_largest_fragment():
    assert (
        pick_primary_panel(
            ["p028_02", "p024_03"],
            {"p028_02": {"bbox_w": 120, "bbox_h": 80}, "p024_03": {"bbox_w": 613, "bbox_h": 818}},
        )
        == "p024_03"
    )
    assert order_source_panels(
        ["p028_02", "p024_03"],
        {"p028_02": {"bbox_w": 120, "bbox_h": 80}, "p024_03": {"bbox_w": 613, "bbox_h": 818}},
    ) == ["p024_03", "p028_02"]


def test_offset_subtitles():
    merged = offset_subtitles(
        [
            (0.0, "1\n00:00:00,000 --> 00:00:01,000\nhello\n"),
            (2.0, "1\n00:00:00,000 --> 00:00:00,500\nthere\n"),
        ]
    )
    assert "00:00:02,000 --> 00:00:02,500" in merged
