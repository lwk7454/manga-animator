"""R2 — shared application services over the stores (API and CLI call these).

One service layer for both surfaces: no ComfyUI contact, no execution here —
pure domain operations over the transactional stores.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..storage.asset_store import AssetStore
from ..storage.database import Database
from ..storage.errors import NotFound
from ..storage.shot_store import ShotStore


@dataclass
class AppContext:
    """One data root's stores. Constructed per request by the API; owned by
    the same process as the future supervised worker (single execution owner)."""

    data_dir: Path
    db: Database
    assets: AssetStore
    shots: ShotStore

    @classmethod
    def open(cls, data_dir: Path) -> AppContext:
        db = Database(Path(data_dir))
        db.initialize()
        assets = AssetStore(db)
        return cls(data_dir=Path(data_dir), db=db, assets=assets, shots=ShotStore(db, assets))


class AssetService:
    """Approvals and bindings — the R2 vertical slice."""

    def __init__(self, ctx: AppContext):
        self.ctx = ctx

    def list_assets(self, series_id: str) -> list[dict[str, Any]]:
        out = []
        for a in self.ctx.assets.list_assets(series_id):
            d = dict(a)
            if d["active_revision"]:
                try:
                    rev = self.ctx.assets.get_revision(d["id"], d["active_revision"])
                    d["active_payload"] = rev["payload"]
                    d["active_artifact_ids"] = rev["artifact_ids"]
                    d["approval"] = rev["approval"]
                except NotFound:
                    d["active_payload"] = None
            out.append(d)
        return out

    def propose_revision(
        self,
        asset_id: str,
        payload: dict[str, Any],
        *,
        origin: str = "generated",
        artifact_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.ctx.assets.add_revision(
            asset_id, payload, origin=origin, artifact_ids=artifact_ids
        )

    def approve(
        self,
        asset_id: str,
        revision: int,
        *,
        approved_by: str,
        expected_asset_revision: int,
        note: str = "",
    ) -> dict[str, Any]:
        """Expected-revision CAS: stale UI edits get RevisionConflict."""
        return self.ctx.assets.approve_revision(
            asset_id,
            revision,
            approved_by=approved_by,
            note=note,
            expected_asset_revision=expected_asset_revision,
        )

    def reject(self, asset_id: str, revision: int, *, note: str = "") -> dict:
        return self.ctx.assets.reject_revision(asset_id, revision, note=note)

    def revoke(self, asset_id: str, revision: int, *, note: str = "") -> dict:
        return self.ctx.assets.revoke_approval(asset_id, revision, note=note)

    def bind_to_panel(
        self, series_id: str, panel_id: str, asset_id: str, asset_revision: int
    ) -> dict:
        return self.ctx.assets.bind_occurrence(
            series_id, panel_id=panel_id, asset_id=asset_id, asset_revision=asset_revision
        )

    def bind_to_shot(
        self, series_id: str, shot_id: str, asset_id: str, asset_revision: int
    ) -> dict:
        return self.ctx.assets.bind_occurrence(
            series_id, shot_id=shot_id, asset_id=asset_id, asset_revision=asset_revision
        )

    def mark_unknown(
        self, series_id: str, panel_id: str | None, shot_id: str | None, note: str = ""
    ) -> dict:
        return self.ctx.assets.bind_unknown(
            series_id, panel_id=panel_id, shot_id=shot_id, note=note
        )

    def dependency_impact(self, asset_id: str) -> dict[str, Any]:
        """Preview which panels/shots are bound to this asset (§C invalidation)."""
        self.ctx.assets.get_asset(asset_id)  # 404 when missing
        with self.ctx.db.transaction() as conn:
            rows = conn.execute(
                "SELECT subject_kind, COALESCE(panel_id, shot_id) AS subject,"
                " asset_revision, state FROM asset_occurrence_bindings"
                " WHERE asset_id=? ORDER BY updated_at",
                (asset_id,),
            ).fetchall()
        return {"asset_id": asset_id, "bindings": [dict(r) for r in rows]}


__all__ = ["AppContext", "AssetService"]
