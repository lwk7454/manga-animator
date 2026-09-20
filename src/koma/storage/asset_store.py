"""R1 — asset store: versioned assets, revisions, approvals, bindings.

Typed store API over the v2 schema. Every mutable method returns typed
outcomes (§ existing storage contract); approvals are explicit human actions;
bindings carry exact revisions; revoked approvals block production binding.
"""

from __future__ import annotations

import json
from typing import Any

from ..util import new_id, utcnow_iso
from .errors import (
    BlockedPrerequisite,
    DuplicateOperation,
    InvalidTransition,
    NotFound,
    RevisionConflict,
)

ASSET_KINDS = ("character", "object", "background")
APPROVAL_STATES = ("draft", "awaiting_review", "approved", "rejected", "superseded")
BINDING_STATES = ("bound", "unknown", "ambiguous")


class AssetStore:
    """Versioned assets + approvals + occurrence bindings (PLAN R1/§C)."""

    def __init__(self, db):
        self.db = db

    # ── assets ────────────────────────────────────────────────────────────
    def create_asset(
        self,
        series_id: str,
        kind: str,
        display_name: str,
        *,
        importance: str = "normal",
        source_occurrences: list[str] | None = None,
    ) -> dict[str, Any]:
        if kind not in ASSET_KINDS:
            raise ValueError(f"kind must be one of {ASSET_KINDS}")
        asset_id = f"asset:{new_id()}"
        now = utcnow_iso()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM assets WHERE series_id=? AND kind=? AND display_name=?",
                (series_id, kind, display_name),
            ).fetchone()
            if row:
                raise DuplicateOperation(f"asset {kind}/{display_name} already exists")
            conn.execute(
                "INSERT INTO assets (id, series_id, kind, display_name, importance,"
                " source_occurrences, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (asset_id, series_id, kind, display_name, importance,
                 json.dumps(source_occurrences or []), now, now),
            )
        return {"asset_id": asset_id, "kind": kind, "display_name": display_name}

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM assets WHERE id=?", (asset_id,)
            ).fetchone()
        if row is None:
            raise NotFound(f"asset {asset_id}")
        d = dict(row)
        d["source_occurrences"] = json.loads(d["source_occurrences"])
        return d

    def list_assets(self, series_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        with self.db.transaction() as conn:
            if kind:
                rows = conn.execute(
                    "SELECT * FROM assets WHERE series_id=? AND kind=? ORDER BY display_name",
                    (series_id, kind),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM assets WHERE series_id=? ORDER BY display_name",
                    (series_id,),
                ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["source_occurrences"] = json.loads(d["source_occurrences"])
            out.append(d)
        return out

    def set_importance(self, asset_id: str, importance: str, *, expected_revision: int) -> int:
        if importance not in ("normal", "story", "required"):
            raise ValueError("importance must be normal|story|required")
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT revision FROM assets WHERE id=?", (asset_id,)
            ).fetchone()
            if row is None:
                raise NotFound(f"asset {asset_id}")
            rev = int(row["revision"])
            if rev != expected_revision:
                raise RevisionConflict(
                    f"asset {asset_id} at revision {rev}, not {expected_revision}")
            conn.execute(
                "UPDATE assets SET importance=?, revision=revision+1, updated_at=? WHERE id=?",
                (importance, utcnow_iso(), asset_id),
            )
            return rev + 1

    # ── revisions ─────────────────────────────────────────────────────────
    def add_revision(
        self,
        asset_id: str,
        payload: dict[str, Any],
        *,
        origin: str = "generated",
        artifact_ids: list[str] | None = None,
        parent_revision: int | None = None,
    ) -> dict[str, Any]:
        if origin not in ("uploaded", "generated", "registry-v1"):
            raise ValueError("origin must be uploaded|generated|registry-v1")
        now = utcnow_iso()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT active_revision FROM assets WHERE id=?", (asset_id,)
            ).fetchone()
            if row is None:
                raise NotFound(f"asset {asset_id}")
            rev = (row["active_revision"] or 0) + 1
            rev_id = f"arev:{new_id()}"
            conn.execute(
                "INSERT INTO asset_revisions (id, asset_id, revision, payload, origin,"
                " artifact_ids, parent_revision, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (rev_id, asset_id, rev, json.dumps(payload, sort_keys=True),
                 origin, json.dumps(artifact_ids or []), parent_revision, now),
            )
            conn.execute(
                "UPDATE assets SET active_revision=?, updated_at=? WHERE id=?",
                (rev, now, asset_id),
            )
            conn.execute(
                "INSERT OR IGNORE INTO asset_approvals (id, asset_id, revision, state,"
                " created_at) VALUES (?,?,?,?,?)",
                (f"appr:{new_id()}", asset_id, rev, "awaiting_review", now),
            )
        return {"revision_id": rev_id, "revision": rev, "state": "awaiting_review"}

    def get_revision(self, asset_id: str, revision: int) -> dict[str, Any]:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM asset_revisions WHERE asset_id=? AND revision=?",
                (asset_id, revision),
            ).fetchone()
            appr = conn.execute(
                "SELECT state, decision_note, approved_by, approved_at FROM asset_approvals"
                " WHERE asset_id=? AND revision=?",
                (asset_id, revision),
            ).fetchone()
        if row is None:
            raise NotFound(f"asset {asset_id} revision {revision}")
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        d["artifact_ids"] = json.loads(d["artifact_ids"])
        d["approval"] = dict(appr) if appr else None
        return d

    # ── approvals (explicit human action only) ────────────────────────────
    def approve_revision(
        self, asset_id: str, revision: int, *, approved_by: str, note: str = ""
    ) -> dict[str, Any]:
        now = utcnow_iso()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT state FROM asset_approvals WHERE asset_id=? AND revision=?",
                (asset_id, revision),
            ).fetchone()
            if row is None:
                raise NotFound(f"approval for {asset_id} r{revision}")
            if row["state"] in ("approved", "superseded"):
                raise InvalidTransition(f"revision already {row['state']}")
            if row["state"] == "rejected":
                raise InvalidTransition("rejected revision cannot be approved; add a new one")
            conn.execute(
                "UPDATE asset_approvals SET state='approved', approved_by=?,"
                " approved_at=?, decision_note=? WHERE asset_id=? AND revision=?",
                (approved_by, now, note, asset_id, revision),
            )
            conn.execute(
                "UPDATE assets SET active_revision=?, updated_at=? WHERE id=?",
                (revision, now, asset_id),
            )
        return {"asset_id": asset_id, "revision": revision, "state": "approved"}

    def reject_revision(self, asset_id: str, revision: int, *, note: str = "") -> dict:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT state FROM asset_approvals WHERE asset_id=? AND revision=?",
                (asset_id, revision),
            ).fetchone()
            if row is None:
                raise NotFound(f"approval for {asset_id} r{revision}")
            if row["state"] == "approved":
                raise InvalidTransition("approved revision cannot be rejected; revoke first")
            conn.execute(
                "UPDATE asset_approvals SET state='rejected', decision_note=?"
                " WHERE asset_id=? AND revision=?",
                (note, asset_id, revision),
            )
        return {"asset_id": asset_id, "revision": revision, "state": "rejected"}

    def revoke_approval(self, asset_id: str, revision: int, *, note: str = "") -> dict:
        """Revocation makes downstream current deliverables stale (§C invalidation)."""
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT state FROM asset_approvals WHERE asset_id=? AND revision=?",
                (asset_id, revision),
            ).fetchone()
            if row is None:
                raise NotFound(f"approval for {asset_id} r{revision}")
            if row["state"] != "approved":
                raise InvalidTransition(f"cannot revoke from state {row['state']}")
            conn.execute(
                "UPDATE asset_approvals SET state='superseded', decision_note=?"
                " WHERE asset_id=? AND revision=?",
                (note, asset_id, revision),
            )
        return {"asset_id": asset_id, "revision": revision, "state": "superseded"}

    def require_approved(self, asset_id: str, revision: int) -> None:
        """Production gate: exact approved revision or BlockedPrerequisite."""
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT state FROM asset_approvals WHERE asset_id=? AND revision=?",
                (asset_id, revision),
            ).fetchone()
        if row is None or row["state"] != "approved":
            state = row["state"] if row else "missing"
            raise BlockedPrerequisite(
                f"asset {asset_id} r{revision} is {state}; production requires an approved revision"
            )

    # ── occurrence bindings (exact revisions; explicit unknown/ambiguous) ──
    def bind_occurrence(
        self,
        series_id: str,
        *,
        panel_id: str | None = None,
        shot_id: str | None = None,
        asset_id: str,
        asset_revision: int,
    ) -> dict[str, Any]:
        if (panel_id is None) == (shot_id is None):
            raise ValueError("exactly one of panel_id/shot_id is required")
        self.require_approved(asset_id, asset_revision)
        subject_kind = "panel" if panel_id else "shot"
        binding_id = f"bind:{new_id()}"
        now = utcnow_iso()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(binding_revision), 0) AS max_rev"
                " FROM asset_occurrence_bindings WHERE asset_id=? AND subject_kind=?"
                " AND COALESCE(panel_id, shot_id)=?",
                (asset_id, subject_kind, panel_id or shot_id),
            ).fetchone()
            brev = int(row["max_rev"]) + 1
            conn.execute(
                "INSERT INTO asset_occurrence_bindings (id, series_id, subject_kind,"
                " panel_id, shot_id, asset_id, asset_revision, state, binding_revision,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (binding_id, series_id, subject_kind, panel_id, shot_id,
                 asset_id, asset_revision, "bound", brev, now, now),
            )
        return {"binding_id": binding_id, "binding_revision": brev, "state": "bound"}

    def bind_unknown(
        self, series_id: str, *, panel_id: str | None = None, shot_id: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Explicit unknown/ambiguous binding blocks production until resolved."""
        if (panel_id is None) == (shot_id is None):
            raise ValueError("exactly one of panel_id/shot_id is required")
        subject_kind = "panel" if panel_id else "shot"
        binding_id = f"bind:{new_id()}"
        now = utcnow_iso()
        state = "unknown" if not note else "ambiguous"
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO asset_occurrence_bindings (id, series_id, subject_kind,"
                " panel_id, shot_id, asset_id, asset_revision, state, binding_revision,"
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (binding_id, series_id, subject_kind, panel_id, shot_id,
                 None, 0, state, 1, now, now),
            )
        return {"binding_id": binding_id, "state": state}

    def bindings_for(self, *, panel_id: str | None = None,
                     shot_id: str | None = None) -> list[dict]:
        if (panel_id is None) == (shot_id is None):
            raise ValueError("exactly one of panel_id/shot_id is required")
        col = "panel_id" if panel_id else "shot_id"
        with self.db.transaction() as conn:
            rows = conn.execute(
                f"SELECT * FROM asset_occurrence_bindings WHERE {col}=?"
                " ORDER BY binding_revision",
                (panel_id or shot_id,),
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            if d["asset_id"] is not None:
                d["approval_state"] = self._approval_state(d["asset_id"], d["asset_revision"])
            out.append(d)
        return out

    def _approval_state(self, asset_id: str, revision: int) -> str | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT state FROM asset_approvals WHERE asset_id=? AND revision=?",
                (asset_id, revision),
            ).fetchone()
        return row["state"] if row else None


__all__ = ["APPROVAL_STATES", "ASSET_KINDS", "BINDING_STATES", "AssetStore"]
