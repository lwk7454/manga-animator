"""Revisioned crop geometry. SQLite is authoritative; JSON is an exchange format."""

from __future__ import annotations

import json

from ..crops import Annotation, bounds, digest
from ..util import new_id, utcnow_iso
from .errors import BlockedPrerequisite, NotFound, RevisionConflict


def crop_blockers(conn, panel_ids: list[str]) -> list[str]:
    """Legacy pages without annotations retain their existing gate behavior."""
    blockers = []
    for pid in panel_ids:
        row = conn.execute(
            "SELECT r.decision_status FROM crop_annotations c "
            "JOIN panels p ON p.page_id=c.page_id JOIN reviews r ON r.id=c.review_id "
            "WHERE p.id=? ORDER BY c.revision DESC LIMIT 1",
            (pid,),
        ).fetchone()
        if row is not None and row[0] != "approved":
            blockers.append(f"panel {pid}: crop masks/ownership/order need review")
    return blockers


class CropStore:
    def __init__(self, db):
        self.db = db

    def latest(self, page_id: str) -> dict | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT c.*, r.decision_status, r.actor FROM crop_annotations c "
                "JOIN reviews r ON r.id=c.review_id WHERE page_id=? "
                "ORDER BY revision DESC LIMIT 1",
                (page_id,),
            ).fetchone()
        return dict(row) if row else None

    def submit(self, page_id: str, annotation: Annotation, *, expected_revision: int) -> int:
        annotation = Annotation.model_validate_json(annotation.canonical_bytes())
        # Rasterization and hashing happen outside the short transaction.
        masks = annotation.ownership()
        boxes = {pid: bounds(mask) for pid, mask in masks.items()}
        payload = annotation.canonical_bytes()
        sha = digest(payload)
        now = utcnow_iso()
        with self.db.transaction(immediate=True) as conn:
            page = conn.execute(
                "SELECT * FROM pages WHERE id=? AND deleted=0", (page_id,)
            ).fetchone()
            if page is None:
                raise NotFound(f"page {page_id}")
            if (page["source_sha256"], page["width"], page["height"]) != (
                annotation.source_sha256,
                annotation.width,
                annotation.height,
            ):
                raise ValueError("annotation source identity differs from canonical page")
            ids = {
                r[0]
                for r in conn.execute(
                    "SELECT id FROM panels WHERE page_id=? AND deleted=0", (page_id,)
                )
            }
            if ids != set(annotation.reading_order):
                raise ValueError("annotation must cover every active panel on this page")
            previous = conn.execute(
                "SELECT revision, annotation_hash, payload FROM crop_annotations "
                "WHERE page_id=? ORDER BY revision DESC LIMIT 1",
                (page_id,),
            ).fetchone()
            revision = previous["revision"] if previous else 0
            if revision != expected_revision:
                raise RevisionConflict(f"crop revision {expected_revision} != {revision}")
            if previous and previous["annotation_hash"] == sha:
                return revision  # unchanged replay never reopens approval
            if previous:
                old = Annotation.model_validate_json(previous["payload"])
                missing = {c.id for c in old.components} - {
                    c.id for c in annotation.components
                } - set(annotation.dismissed)
                if missing or set(old.proposals) - set(annotation.proposals):
                    raise ValueError(
                        "detected objects cannot disappear without a dismissal rationale"
                    )
            revision += 1
            review_id = new_id()
            conn.execute(
                "INSERT INTO reviews (id, series_id, scope_key, kind,"
                "expected_aggregate_revision, evidence_manifest, proposed_desired_state,"
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    review_id,
                    page["series_id"],
                    page_id,
                    "panel_crops",
                    revision,
                    json.dumps({"annotation_hash": sha}),
                    payload.decode(),
                    now,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO crop_annotations VALUES (?,?,?,?,?)",
                (page_id, revision, payload.decode(), sha, review_id),
            )
            for order, pid in enumerate(annotation.reading_order, 1):
                box = boxes[pid]
                conn.execute(
                    "UPDATE panels SET bbox_x=?, bbox_y=?, bbox_w=?, bbox_h=?,"
                    "crop_revision=crop_revision+1, reading_order=?, updated_at=? WHERE id=?",
                    (*[box[k] for k in ("x", "y", "w", "h")], order, now, pid),
                )
            conn.execute(
                "UPDATE pages SET order_review_status='open',"
                "order_review_revision=order_review_revision+1, updated_at=? WHERE id=?",
                (now, page_id),
            )
        return revision

    def approve(self, page_id: str, *, expected_revision: int, actor: str) -> None:
        if not actor.strip():
            raise ValueError("a named human reviewer is required")
        row = self.latest(page_id)
        if row is None:
            raise NotFound(f"crop annotation for {page_id}")
        annotation = Annotation.model_validate_json(row["payload"])
        annotation.ownership()
        if annotation.needs_review:
            raise BlockedPrerequisite("resolve all boundary, ownership and order flags first")
        with self.db.transaction(immediate=True) as conn:
            latest = conn.execute(
                "SELECT MAX(revision) FROM crop_annotations WHERE page_id=?",
                (page_id,),
            ).fetchone()[0]
            if latest != expected_revision or row["revision"] != latest:
                raise RevisionConflict("crop revision changed before approval")
            conn.execute(
                "UPDATE reviews SET decision_status='approved', actor=?, final_decision=?,"
                "updated_at=? WHERE id=?",
                (
                    actor,
                    json.dumps({"annotation_hash": row["annotation_hash"]}),
                    utcnow_iso(),
                    row["review_id"],
                ),
            )

    def require_approved(self, panel_ids: list[str]) -> None:
        with self.db.transaction() as conn:
            blockers = crop_blockers(conn, panel_ids)
        if blockers:
            raise BlockedPrerequisite("; ".join(blockers))
