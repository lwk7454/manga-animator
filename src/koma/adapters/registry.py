"""Model-adapter registry — specs, not code (PLAN §11, decision #014/#020/#021).

Two spec layers under ``config/models/``:
- ``components/<id>.toml`` — one weights artifact: file, sha256, target dir,
  provenance. Hash-verified at install (community artifacts have a scam
  history); ``koma doctor`` re-verifies presence.
- ``profiles/<id>.toml`` — a lane: workflow JSON + inject map + frame grid +
  component references. Swapping a model = add TOMLs + one config line.

v1 ships one real GPU profile: ``manganinja_sd15`` (colorize lane). The video
profiles (stock H3 + PinkCherry/Heretic recipes) follow in Phase 5.
"""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from typing import Any

from ..log import get_logger

log = get_logger("adapters")


class AdapterError(RuntimeError):
    pass


def _models_dir(config_dir: Path) -> Path:
    return Path(config_dir) / "models"


def load_spec(path: Path) -> dict[str, Any]:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def load_profile(config_dir: Path, profile_id: str) -> dict[str, Any]:
    path = _models_dir(config_dir) / "profiles" / f"{profile_id}.toml"
    if not path.exists():
        raise AdapterError(f"profile {profile_id!r} not found at {path}")
    return load_spec(path)


def load_component(config_dir: Path, component_id: str) -> dict[str, Any]:
    path = _models_dir(config_dir) / "components" / f"{component_id}.toml"
    if not path.exists():
        raise AdapterError(f"component {component_id!r} not found at {path}")
    return load_spec(path)


def list_profiles(config_dir: Path) -> list[dict[str, Any]]:
    """All profiles with their ids and kinds (for ``koma models list``)."""
    out = []
    pdir = _models_dir(config_dir) / "profiles"
    if not pdir.exists():
        return out
    for path in sorted(pdir.glob("*.toml")):
        spec = load_spec(path)
        out.append({"id": spec.get("id", path.stem), "kind": spec.get("kind", "?"), "path":
            str(path)})
    return out


def verify_component(config_dir: Path, component_id: str, box_model_root: str = "") -> bool:
    """Check a component's weights file exists; verify sha256 if the file is local.

    The box's model tree isn't visible to the Mac — presence is verified over
    SSH by the caller (``koma doctor``); here we verify hash when the file IS
    reachable at ``box_model_root`` (e.g. a mounted share) and otherwise only
    check the spec is complete.
    """
    spec = load_component(config_dir, component_id)
    file = spec.get("file")
    sha = spec.get("sha256", "")
    if not file:
        raise AdapterError(f"component {component_id}: missing 'file'")
    if not sha or len(sha) != 64:
        log.warning("component %s: sha256 not pinned yet — pin it at first install", component_id)
    if box_model_root:
        candidate = Path(box_model_root) / spec.get("target_dir", "") / file
        if candidate.exists() and sha:
            actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if actual != sha:
                raise AdapterError(
                    f"component {component_id}: sha256 mismatch ({candidate.name}) — refuse to use"
                )
    return True


def resolve_profile(config_dir: Path, profile_id: str) -> dict[str, Any]:
    """Load a profile + its workflow JSON; returns {spec, workflow, inject}."""
    spec = load_profile(config_dir, profile_id)
    repo_root = Path(config_dir).parent
    wf_path = repo_root / "config" / spec.get("workflow", "")
    if not wf_path.exists():
        raise AdapterError(f"profile {profile_id}: workflow file missing: {wf_path}")
    import json

    workflow = json.loads(wf_path.read_text(encoding="utf-8"))
    return {"spec": spec, "workflow": workflow, "inject": spec.get("inject", {})}
