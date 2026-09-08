"""Config layering (vidgen doctrine): config.toml → environment → CLI flags.

Paths in config.toml are RELATIVE TO THE PACKAGE ROOT (repo root, since the
package lives in src/ and the repo ships config/ at its top). This is the
inherited vidgen TOML trap — do not "fix" it.

Secrets NEVER live in config.toml (committed): they come from env only.
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent  # src/koma → repo root

DEFAULT_CONFIG = PACKAGE_ROOT / "config" / "config.toml"


class Settings:
    """Layered settings. Precedence: env var > config.toml > builtin default."""

    def __init__(self, config_path: Path | None = None):
        self._path = config_path or DEFAULT_CONFIG
        self._data: dict = {}
        if self._path.exists():
            with open(self._path, "rb") as f:
                self._data = tomllib.load(f)

    # ── generic access ───────────────────────────────────────────────────────
    def get(self, dotted: str, default: object = None) -> object:
        node: object = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def env(self, key: str, default: str | None = None) -> str | None:
        return os.environ.get(key, default)

    # ── paths (package-root-relative in TOML, per vidgen trap) ──────────────
    def resolved_data_dir(self) -> Path:
        raw = str(self.get("paths.data", "data"))
        p = Path(raw)
        return p if p.is_absolute() else (PACKAGE_ROOT / p)

    def resolved_config_dir(self) -> Path:
        raw = str(self.get("paths.config", "config"))
        p = Path(raw)
        return p if p.is_absolute() else (PACKAGE_ROOT / p)

    # ── model routing (adapter registry fills this in later phases) ─────────
    @property
    def video_profile_default(self) -> str:
        return str(self.get("video.profile_default", "h3_fl2va_turbo"))

    @property
    def text_llm(self) -> str:
        return str(self.get("text.llm", "glm-5.3-flash"))

    @property
    def vision_base_url(self) -> str | None:
        """Vision endpoint base URL; None = legacy local Ollama on the Mac."""
        raw = self.get("vision.base_url")
        return str(raw) if raw else None

    @property
    def vision_model(self) -> str:
        return str(self.get("vision.model", "qwen2.5-vl-7b"))

    @property
    def gpu_host(self) -> str:
        return str(self.get("gpu.host", "192.168.50.103"))

    @property
    def gpu_port(self) -> int:
        return int(str(self.get("gpu.port", 8188)))


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
