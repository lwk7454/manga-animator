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
    """Return True only for a reachable file with a matching pinned SHA-256.

    No model root or no hash means unverified (False). Missing files, malformed
    hashes and mismatches raise AdapterError. This does not probe remote weights.
    """
    spec = load_component(config_dir, component_id)
    file = spec.get("file")
    sha = spec.get("sha256", "")
    if not file:
        raise AdapterError(f"component {component_id}: missing 'file'")
    if sha and (len(sha) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha)):
        raise AdapterError(f"component {component_id}: invalid sha256")
    if not sha:
        log.warning("component %s: sha256 not pinned yet — pin it at first install", component_id)
    if box_model_root:
        candidate = Path(box_model_root) / spec.get("target_dir", "") / file
        if not candidate.is_file():
            raise AdapterError(f"component {component_id}: weights file missing: {candidate}")
        if sha:
            with candidate.open("rb") as fh:
                actual = hashlib.file_digest(fh, "sha256").hexdigest()
            if actual != sha.lower():
                raise AdapterError(
                    f"component {component_id}: sha256 mismatch ({candidate.name}) — refuse to use"
                )
            return True
    return False


def resolve_profile(config_dir: Path, profile_id: str) -> dict[str, Any]:
    """Load a profile + its workflow JSON; returns {spec, workflow, inject}."""
    spec = load_profile(config_dir, profile_id)
    wf_path = Path(config_dir) / spec.get("workflow", "")
    if not wf_path.exists():
        raise AdapterError(f"profile {profile_id}: workflow file missing: {wf_path}")
    import json

    workflow = json.loads(wf_path.read_text(encoding="utf-8"))
    inject = spec.get("inject", {})
    if spec.get("kind") == "video_i2v":
        required = {"keyframe", "motion", "length", "fps", "seed", "width", "height",
                    "steps", "output"}
        missing = required - inject.keys()
        if missing:
            raise AdapterError(f"profile {profile_id}: missing video slots: {sorted(missing)}")
        for name in required:
            path = inject[name]
            value = workflow
            try:
                for part in path.split("."):
                    value = value[part]
            except (KeyError, TypeError, AttributeError) as exc:
                raise AdapterError(f"profile {profile_id}: invalid {name} slot {path!r}") from exc
            # Scalar parameters may replace linked primitive outputs. An uploaded
            # filename, however, must remain on a loader rather than an IMAGE socket.
            if name == "keyframe" and not isinstance(value, str):
                raise AdapterError(
                    f"profile {profile_id}: keyframe must target a filename, not a connection"
                )
    return {"spec": spec, "workflow": workflow, "inject": inject}
