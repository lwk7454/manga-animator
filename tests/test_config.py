"""Durable feature tests; no live model services."""

from __future__ import annotations

from pathlib import Path

import pytest

from koma.config import Settings


class TestConfig:
    def test_defaults_from_committed_toml(self) -> None:
        s = Settings()
        assert s.video_profile_default == "minimax_h3_fl2va_turbo"
        assert s.text_llm == "qwen2.5-vl-7b"
        assert s.gpu_host == "192.168.50.103"
        assert s.gpu_port == 8188

    def test_paths_resolve_to_package_root(self) -> None:
        s = Settings()
        dd = s.resolved_data_dir()
        assert dd.is_absolute()
        assert dd.name == "data"

    def test_missing_config_uses_builtins(self, tmp_path: Path) -> None:
        s = Settings(tmp_path / "nonexistent.toml")
        assert s.video_profile_default == "minimax_h3_fl2va_turbo"

    def test_env_layering(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "c.toml").write_text("[text]\nllm = 'from-file'\n")
        s = Settings(tmp_path / "c.toml")
        assert s.text_llm == "from-file"
        assert s.env("KOMA_TEXT_API_KEY") is None
        monkeypatch.setenv("KOMA_TEXT_API_KEY", "secret-value")
        assert s.env("KOMA_TEXT_API_KEY") == "secret-value"  # env layer wins at call sites
