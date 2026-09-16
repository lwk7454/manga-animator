"""Immutable artifact staging, validation and promotion (PLAN.md §6).

Artifact directories are immutable after validation::

    series/<slug>/artifacts/<artifact_uuid>/payload.<ext>
    series/<slug>/artifacts/<artifact_uuid>/manifest.json

Write flow (§6.1): staged payload (attempt-local) → flush/close → hash
(OUTSIDE any transaction) → rename into the immutable directory → write
manifest → THEN DB-promote (artifact rows + current-output pointers in one
transaction via the domain store). A crash after rename but before DB commit
leaves an orphan candidate; recovery inspects its manifest and may adopt it
or leave it unselected — the previously accepted output stays valid.

Media validation is the CALLER's job (§19.1); this module assumes validated
inputs and provides the durable identity/promotion mechanics.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

from ..log import get_logger
from ..util import utcnow_iso
from .database import Database
from .errors import NotFound

log = get_logger("storage.artifacts")

MANIFEST_SCHEMA_VERSION = 1


class ArtifactStore:
    """Filesystem side of the artifact protocol for one data root."""

    def __init__(self, data_dir: Path, db: Database | None = None):
        self.data_dir = Path(data_dir)
        self.db = db

    def artifacts_root(self, slug: str) -> Path:
        return self.data_dir / "series" / slug / "artifacts"

    # ── staging (attempt-scoped, same filesystem as the immutable root) ──────
    def staging_dir(self, slug: str, attempt_id: str) -> Path:
        p = self.data_dir / "series" / slug / "_staging" / attempt_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def write_staged_payload(
        self,
        slug: str,
        attempt_id: str,
        output_role: str,
        payload: bytes,
        *,
        ext: str = "png",
    ) -> Path:
        """Write one staged output file (attempt-local; never a canonical path)."""
        p = self.staging_dir(slug, attempt_id) / f"{output_role}.{ext}"
        p.write_bytes(payload)
        return p

    # ── promotion (§6.1/§6.2) ────────────────────────────────────────────────
    def finalize_attempt(
        self,
        slug: str,
        attempt_id: str,
        owner_epoch: str,
        expected_fingerprint: str,
        validated: list[dict],
    ) -> dict:
        """Hash staged outputs, rename into immutable dirs, then DB-promote.

        Each ``validated`` entry: {output_role, kind, staged_path (relative to
        the attempt staging dir), media_metadata, result_metadata,
        dependencies}. Multiple outputs from one attempt promote together.
        """
        if self.db is None:
            raise ValueError("ArtifactStore needs a Database to promote")
        from .domain_store import KomaStore

        store = KomaStore(self.db)
        staging = self.staging_dir(slug, attempt_id)
        artifacts: list[dict] = []
        for entry in validated:
            src = staging / entry["staged_path"]
            if not src.is_file():
                raise NotFound(f"staged output missing: {src}")
            artifact_uuid = str(uuid.uuid4())
            final_dir = self.artifacts_root(slug) / artifact_uuid
            suffix = Path(entry["staged_path"]).suffix.lstrip(".") or "bin"
            payload_name = f"payload.{suffix}"
            final_dir.mkdir(parents=True, exist_ok=True)
            sha = file_digest(src)  # hashing happens outside transactions
            os.replace(src, final_dir / payload_name)
            manifest = {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "artifact_uuid": artifact_uuid,
                "attempt_id": attempt_id,
                "output_role": entry["output_role"],
                "kind": entry["kind"],
                "input_fingerprint": expected_fingerprint,
                "sha256": sha,
                "size": (final_dir / payload_name).stat().st_size,
                "media_metadata": entry.get("media_metadata", {}),
                "result_metadata": entry.get("result_metadata", {}),
                "dependencies": entry.get("dependencies", []),
                "created_at": utcnow_iso(),
            }
            (final_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            rel = str(final_dir.relative_to(self.data_dir))
            artifacts.append(
                {
                    "kind": entry["kind"],
                    "relative_path": rel,
                    "sha256": sha,
                    "size": manifest["size"],
                    "output_role": entry["output_role"],
                    "media_metadata": entry.get("media_metadata", {}),
                    "result_metadata": entry.get("result_metadata", {}),
                    "dependencies": entry.get("dependencies", []),
                }
            )
        # attempt-local staging removed only after successful rename
        shutil.rmtree(staging, ignore_errors=True)
        return store.promote_artifacts(
            attempt_id, owner_epoch, expected_fingerprint, artifacts
        )

    def payload_path(self, slug: str, artifact_uuid: str) -> Path:
        d = self.artifacts_root(slug) / artifact_uuid
        if not d.is_dir():
            raise NotFound(f"artifact {artifact_uuid}")
        payloads = [p for p in d.iterdir() if p.name.startswith("payload.")]
        if not payloads:
            raise NotFound(f"payload for artifact {artifact_uuid}")
        return payloads[0]

    def read_manifest(self, slug: str, artifact_uuid: str) -> dict:
        p = self.artifacts_root(slug) / artifact_uuid / "manifest.json"
        if not p.exists():
            raise NotFound(f"manifest for artifact {artifact_uuid}")
        return json.loads(p.read_text(encoding="utf-8"))

    # ── orphan recovery (§9.5 row 8) ─────────────────────────────────────────
    def find_orphans(self, slug: str) -> list[dict]:
        """Artifact dirs present on disk but not committed in the DB.

        Orphans are never deleted automatically; recovery may adopt them
        (manifest complete + attempt known) or leave them unselected while
        the previous accepted output stays valid.
        """
        root = self.artifacts_root(slug)
        if not root.exists() or self.db is None:
            return []
        from .domain_store import KomaStore

        store = KomaStore(self.db)
        known: set[str] = set()
        series = store.get_series_by_slug(slug)
        if series is None:
            return []
        with self.db.transaction() as conn:
            for r in conn.execute(
                "SELECT relative_path FROM artifacts WHERE series_id = ?", (series["id"],)
            ).fetchall():
                known.add(r["relative_path"])
        out: list[dict] = []
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            rel = str(d.relative_to(self.data_dir))
            if rel in known:
                continue
            manifest_path = d / "manifest.json"
            if manifest_path.exists():
                out.append(
                    {
                        "relative_path": rel,
                        "manifest": json.loads(manifest_path.read_text(encoding="utf-8")),
                    }
                )
            else:
                out.append({"relative_path": rel, "manifest": None})
        return out

    def adopt_orphan(self, slug: str, relative_path: str, owner_epoch: str) -> dict:
        """Commit an orphan candidate whose manifest is complete.

        Uses the manifest's recorded fingerprint and attempt; the attempt row
        must exist (its owner epoch is re-fenced by promote_artifacts). An
        unknown attempt leaves the orphan unselected.
        """
        if self.db is None:
            raise ValueError("ArtifactStore needs a Database to adopt")
        manifest = self.read_manifest(slug, Path(relative_path).name)
        from .domain_store import KomaStore

        store = KomaStore(self.db)
        attempt_id = manifest.get("attempt_id") or ""
        try:
            store.get_attempt(attempt_id)
        except NotFound as exc:
            raise NotFound(
                f"orphan {relative_path}: attempt {attempt_id} unknown; leave unselected"
            ) from exc
        artifact = {
            "kind": manifest["kind"],
            "relative_path": relative_path,
            "sha256": manifest["sha256"],
            "size": manifest["size"],
            "output_role": manifest["output_role"],
            "media_metadata": manifest.get("media_metadata", {}),
            "result_metadata": manifest.get("result_metadata", {}),
            "dependencies": [],
        }
        return store.promote_artifacts(
            attempt_id, owner_epoch, manifest["input_fingerprint"], [artifact]
        )


def file_digest(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


__all__ = ["MANIFEST_SCHEMA_VERSION", "ArtifactStore", "file_digest"]
