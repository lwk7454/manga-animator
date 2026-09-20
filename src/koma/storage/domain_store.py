"""Domain store — transactional reads/mutations over the SQLite schema (§5).

Every mutable method distinguishes not-found / revision-conflict /
invalid-transition / blocked-prerequisite outcomes (typed errors in
``.errors``). Repeating the same successful operation ID returns the original
outcome. Whole-index replacement is FORBIDDEN: writes are transactional,
revision-checked (CAS) mutations.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..models.work import (
    AttemptState,
    WorkState,
    can_attempt_transition,
    can_work_transition,
)
from ..util import canonical_hash, new_id, utcnow_iso
from .database import Database
from .errors import (
    BlockedPrerequisite,
    DuplicateOperation,
    InvalidTransition,
    NotFound,
    RevisionConflict,
)

RATIOS_VALID = {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9"}


def _loads(raw: str | None, default: Any = None) -> Any:
    if raw is None or raw == "":
        return default
    return json.loads(raw)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


class KomaStore:
    """The canonical store API. One instance per process; thread-local conns."""

    def __init__(self, db: Database):
        self.db = db

    @classmethod
    def open(cls, data_dir: Path) -> KomaStore:
        db = Database(data_dir)
        db.initialize()
        return cls(db)

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return conn.execute(sql, params).fetchone()

    @staticmethod
    def _require(conn: sqlite3.Connection, sql: str, params: tuple, entity: str) -> sqlite3.Row:
        row = conn.execute(sql, params).fetchone()
        if row is None:
            raise NotFound(entity)
        return row

    def _bump_revision(
        self, conn: sqlite3.Connection, table: str, id_col: str, entity_id: str
    ) -> int:
        row = self._one(conn, f"SELECT revision FROM {table} WHERE {id_col} = ?", (entity_id,))
        if row is None:
            raise NotFound(f"{table}:{entity_id}")
        new = int(row[0]) + 1
        conn.execute(f"UPDATE {table} SET revision = ?, updated_at = ? WHERE {id_col} = ?",
                     (new, utcnow_iso(), entity_id))
        return new

    # ═══ series / chapters ════════════════════════════════════════════════════

    def create_series(
        self,
        slug: str,
        title: str = "",
        language: str = "ja",
        voice_language: str = "ja",
        required_ratios: list[str] | None = None,
        subtitle_languages: list[str] | None = None,
        video_profile_id: str | None = None,
        production_settings: dict | None = None,
    ) -> str:
        for r in required_ratios or ["16:9", "9:16"]:
            if r not in RATIOS_VALID:
                raise ValueError(f"invalid ratio {r!r}")
        now = utcnow_iso()
        sid = new_id()
        with self.db.transaction(immediate=True) as conn:
            if self._one(conn, "SELECT 1 FROM series WHERE slug = ?", (slug,)):
                raise DuplicateOperation(f"series slug {slug!r} already exists")
            conn.execute(
                "INSERT INTO series (id, slug, title, language, voice_language,"
                " required_ratios, subtitle_languages, video_profile_id,"
                " production_settings, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sid,
                    slug,
                    title,
                    language,
                    voice_language,
                    _dumps(required_ratios or ["16:9", "9:16"]),
                    _dumps(subtitle_languages or ["en", "zh-HANS"]),
                    video_profile_id,
                    _dumps(production_settings or {}),
                    now,
                    now,
                ),
            )
        return sid

    def get_series_by_slug(self, slug: str) -> dict | None:
        with self.db.transaction() as conn:
            row = self._one(conn, "SELECT * FROM series WHERE slug = ?", (slug,))
        return dict(row) if row else None

    def require_series(self, slug: str) -> dict:
        s = self.get_series_by_slug(slug)
        if s is None:
            raise NotFound(f"series {slug!r}")
        return s

    def list_series(self) -> list[dict]:
        with self.db.transaction() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM series ORDER BY slug").fetchall()]

    def set_series_profile_pin(
        self, slug: str, profile_id: str | None, expected_revision: int
    ) -> int:
        """Authoritative profile pin (§15.1): the series row is THE pin.

        Reports the new revision; callers surface affected stages before
        changing it. CAS via expected_revision.
        """
        with self.db.transaction(immediate=True) as conn:
            row = self._one(conn, "SELECT id, revision FROM series WHERE slug = ?", (slug,))
            if row is None:
                raise NotFound(f"series {slug!r}")
            if int(row["revision"]) != expected_revision:
                raise RevisionConflict(
                    f"series revision {expected_revision} != current {row['revision']}"
                )
            conn.execute(
                "UPDATE series SET video_profile_id = ?, revision = revision + 1,"
                " updated_at = ? WHERE id = ?",
                (profile_id, utcnow_iso(), row["id"]),
            )
            return int(row["revision"]) + 1

    def ensure_chapter(self, slug: str, number: int, title: str = "") -> str:
        if number <= 0:
            raise ValueError("chapter number must be positive")
        with self.db.transaction(immediate=True) as conn:
            series = self._one(conn, "SELECT id FROM series WHERE slug = ?", (slug,))
            if series is None:
                raise NotFound(f"series {slug!r}")
            row = self._one(
                conn, "SELECT id FROM chapters WHERE series_id = ? AND number = ?",
                (series["id"], number),
            )
            if row is not None:
                return row["id"]
            cid = new_id()
            conn.execute(
                "INSERT INTO chapters (id, series_id, number, title, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (cid, series["id"], number, title, utcnow_iso(), utcnow_iso()),
            )
            return cid

    def get_chapter(self, slug: str, number: int) -> dict:
        with self.db.transaction() as conn:
            row = self._one(
                conn,
                "SELECT c.* FROM chapters c JOIN series s ON s.id = c.series_id"
                " WHERE s.slug = ? AND c.number = ?",
                (slug, number),
            )
        if row is None:
            raise NotFound(f"chapter {number} of {slug!r}")
        return dict(row)

    # ═══ pages / panels ═══════════════════════════════════════════════════════

    def import_pages(
        self,
        slug: str,
        chapter_number: int,
        entries: list[dict],
        operation_id: str,
        import_source_id: str,
        expected_chapter_revision: int | None = None,
    ) -> dict:
        """Append pages transactionally (D02).

        Each entry: {source_path, sha256, width, height, direction,
        direction_confidence, panels: [{bbox x/y/w/h}]}. Idempotent per
        (chapter, import_source, occurrence_key): identical reimport is a
        no-op; changed hash at the same path is a replacement conflict (must
        go through the explicit replace operation, §11).
        """
        if not entries:
            raise ValueError("no entries to import")
        now = utcnow_iso()
        with self.db.transaction(immediate=True) as conn:
            series = self._one(conn, "SELECT id FROM series WHERE slug = ?", (slug,))
            if series is None:
                raise NotFound(f"series {slug!r}")
            sid = series["id"]
            ch = self._one(
                conn, "SELECT id, revision FROM chapters WHERE series_id = ? AND number = ?",
                (sid, chapter_number),
            )
            if ch is None:
                raise NotFound(f"chapter {chapter_number}")
            if expected_chapter_revision is not None and (
                int(ch["revision"]) != expected_chapter_revision
            ):
                raise RevisionConflict("chapter revision changed since planning")

            # operation idempotency: same operation → return its original outcome
            prior = self._one(
                conn,
                "SELECT page_id, occurrence_key FROM import_entries WHERE operation_id = ?",
                (operation_id,),
            )
            if prior is not None:
                page_ids = sorted({r[0] for r in conn.execute(
                    "SELECT DISTINCT page_id FROM import_entries WHERE operation_id = ?",
                    (operation_id,))})
                return {"operation_id": operation_id, "created": [], "page_ids": page_ids,
                        "idempotent": True}

            appended: list[str] = []
            conflicts: list[str] = []
            seq_row = self._one(
                conn,
                "SELECT COALESCE(MAX(sequence_position), 0) AS m FROM pages WHERE chapter_id = ?",
                (ch["id"],),
            )
            next_seq = int(seq_row["m"] if seq_row else 0) + 1
            birth_row = self._one(
                conn,
                "SELECT COALESCE(MAX(birth_number), 0) AS m FROM pages WHERE chapter_id = ?",
                (ch["id"],),
            )
            next_birth = int(birth_row["m"] if birth_row else 0) + 1

            for idx, entry in enumerate(entries):
                occ = str(entry.get("occurrence_key", entry.get("source_path", f"entry-{idx}")))
                existing = self._one(
                    conn,
                    "SELECT ie.*, p.source_sha256, p.deleted FROM import_entries ie"
                    " JOIN pages p ON p.id = ie.page_id"
                    " WHERE ie.chapter_id = ? AND ie.import_source_id = ?"
                    " AND ie.occurrence_key = ?",
                    (ch["id"], import_source_id, occ),
                )
                if existing:
                    if existing["source_sha256"] == entry["sha256"] and not existing["deleted"]:
                        continue  # unchanged reimport: no-op (D02)
                    conflicts.append(occ)
                    continue
                # new page: append
                page_id = new_id()
                seq = next_seq
                next_seq += 1
                conn.execute(
                    "INSERT INTO pages (id, chapter_id, series_id, birth_number,"
                    " sequence_position, source_path, source_sha256, width, height,"
                    " direction, direction_confidence, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        page_id, ch["id"], sid, next_birth, seq,
                        entry.get("source_path", ""), entry["sha256"],
                        int(entry.get("width", 0)), int(entry.get("height", 0)),
                        entry.get("direction", "rtl"),
                        float(entry.get("direction_confidence", 1.0)),
                        now, now,
                    ),
                )
                next_birth += 1
                conn.execute(
                    "INSERT INTO import_entries (id, chapter_id, import_source_id, page_id,"
                    " occurrence_key, source_sha256, operation_id, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_id(), ch["id"], import_source_id, page_id, occ,
                     entry["sha256"], operation_id, now),
                )
                # panels are minted here with immutable ids provided by the caller
                for p_idx, p in enumerate(entry.get("panels", []), start=1):
                    bbox = p["bbox"]
                    if int(bbox["w"]) <= 0 or int(bbox["h"]) <= 0:
                        raise ValueError(f"invalid bbox for page {seq}: {bbox}")
                    conn.execute(
                        "INSERT INTO panels (id, page_id, series_id, chapter_id,"
                        " birth_ordinal, bbox_x, bbox_y, bbox_w, bbox_h, reading_order,"
                        " created_at, updated_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            p["panel_id"], page_id, sid, ch["id"], p_idx,
                            int(bbox["x"]), int(bbox["y"]), int(bbox["w"]), int(bbox["h"]),
                            int(p.get("reading_order", p_idx)), now, now,
                        ),
                    )
                appended.append(page_id)
            if conflicts:
                raise DuplicateOperation(
                    "changed source at existing import path(s): "
                    + ", ".join(conflicts)
                    + " — use the explicit replace operation (PLAN §11.3)"
                )
            self._bump_revision(conn, "chapters", "id", ch["id"])
            return {"operation_id": operation_id, "created": appended,
                    "page_ids": appended, "idempotent": False}

    def load_pages(
        self, slug: str, chapter_number: int, include_deleted: bool = False
    ) -> list[dict]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT p.* FROM pages p JOIN chapters c ON c.id = p.chapter_id"
                " JOIN series s ON s.id = c.series_id"
                " WHERE s.slug = ? AND c.number = ?"
                + ("" if include_deleted else " AND p.deleted = 0")
                + " ORDER BY p.sequence_position",
                (slug, chapter_number),
            ).fetchall()
        return [dict(r) for r in rows]

    def load_panels(self, slug: str, chapter_number: int) -> list[dict]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT pn.* FROM panels pn"
                " JOIN chapters c ON c.id = pn.chapter_id"
                " JOIN series s ON s.id = c.series_id"
                " WHERE s.slug = ? AND c.number = ? AND pn.deleted = 0"
                " ORDER BY pn.reading_order",
                (slug, chapter_number),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_panel(self, panel_id: str) -> dict:
        with self.db.transaction() as conn:
            row = self._one(conn, "SELECT * FROM panels WHERE id = ?", (panel_id,))
        if row is None or row["deleted"]:
            raise NotFound(f"panel {panel_id!r}")
        return dict(row)

    def set_page_order(
        self,
        page_id: str,
        expected_revision: int,
        desired_panel_ids: list[str],
        operation_id: str,
    ) -> dict:
        """Full desired permutation with CAS + idempotent operation key (§12).

        Never renames IDs or touches crops. A repeated operation ID with the
        identical desired order returns the original successful result.
        """
        now = utcnow_iso()
        with self.db.transaction(immediate=True) as conn:
            page = self._one(conn, "SELECT * FROM pages WHERE id = ?", (page_id,))
            if page is None or page["deleted"]:
                raise NotFound(f"page {page_id!r}")
            current_rev = int(page["order_review_revision"])
            prior = self._one(
                conn, "SELECT * FROM reviews WHERE operation_id = ?", (operation_id,)
            )
            if prior and prior["decision_status"] == "approved":
                return {"operation_id": operation_id, "idempotent": True,
                        "order_review_revision": current_rev}
            if prior and prior["decision_status"] == "open":
                # stale open review from another attempt with a different request
                proposed = json.loads(prior["proposed_desired_state"] or "[]")
                if proposed != desired_panel_ids:
                    raise RevisionConflict(
                        "a conflicting review submission is already open for this operation"
                    )
            if current_rev != expected_revision:
                raise RevisionConflict(
                    f"page order revision {expected_revision} != current {current_rev}"
                )
            active = [r[0] for r in conn.execute(
                "SELECT id FROM panels WHERE page_id = ? AND deleted = 0", (page_id,)
            )]
            if sorted(active) != sorted(desired_panel_ids):
                raise ValueError(
                    "desired order must contain every active panel id exactly once"
                )
            # duplicate id guard beyond the set comparison above
            if len(set(desired_panel_ids)) != len(desired_panel_ids):
                raise ValueError("duplicate panel ids in desired order")
            for pos, pid in enumerate(desired_panel_ids, start=1):
                conn.execute(
                    "UPDATE panels SET reading_order = ?, updated_at = ? WHERE id = ?",
                    (pos, now, pid),
                )
            conn.execute(
                "UPDATE pages SET order_review_revision = order_review_revision + 1,"
                " order_review_status = 'reviewed', updated_at = ? WHERE id = ?",
                (now, page_id),
            )
            if prior:
                conn.execute(
                    "UPDATE reviews SET decision_status = 'approved', final_decision = ?,"
                    " updated_at = ? WHERE id = ?",
                    (_dumps({"order": desired_panel_ids}), now, prior["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO reviews (id, series_id, scope_key, kind,"
                    " expected_aggregate_revision, evidence_manifest,"
                    " proposed_desired_state, final_decision, decision_status,"
                    " operation_id, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?, ?)",
                    (
                        new_id(), page["series_id"], f"page:{page_id}", "order",
                        expected_revision, _dumps({"active_panel_ids": active}),
                        _dumps(desired_panel_ids), _dumps({"order": desired_panel_ids}),
                        operation_id, now, now,
                    ),
                )
            return {
                "operation_id": operation_id,
                "order_review_revision": current_rev + 1,
                "idempotent": False,
            }

    def remove_page(self, page_id: str, operation_id: str) -> dict:
        """Tombstone removal with explicit approval semantics (§11.3)."""
        now = utcnow_iso()
        with self.db.transaction(immediate=True) as conn:
            page = self._one(conn, "SELECT * FROM pages WHERE id = ?", (page_id,))
            if page is None:
                raise NotFound(f"page {page_id!r}")
            if page["deleted"]:
                return {"operation_id": operation_id, "idempotent": True}
            prior = self._one(
                conn, "SELECT 1 FROM reviews WHERE operation_id = ?", (operation_id,)
            )
            if prior:
                return {"operation_id": operation_id, "idempotent": True}
            conn.execute(
                "UPDATE pages SET deleted = 1, updated_at = ? WHERE id = ?", (now, page_id)
            )
            conn.execute(
                "UPDATE panels SET deleted = 1, updated_at = ? WHERE page_id = ?",
                (now, page_id),
            )
            conn.execute(
                "INSERT INTO reviews (id, series_id, scope_key, kind,"
                " expected_aggregate_revision, evidence_manifest, proposed_desired_state,"
                " decision_status, operation_id, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id(), page["series_id"], f"page:{page_id}", "remove_page",
                    int(page["order_review_revision"]), _dumps({"tombstone": True}),
                    _dumps([]), "approved", operation_id, now, now,
                ),
            )
            return {"operation_id": operation_id, "idempotent": False}

    # ═══ registry (§14) ═══════════════════════════════════════════════════════

    def save_registry_proposal(
        self,
        entity_id: str,
        kind: str,
        payload: dict,
        evidence_artifact_ids: list[str] | None = None,
        parent_character_id: str | None = None,
        expected_revision: int | None = None,
    ) -> dict:
        """Append a new proposed revision; approved revisions are immutable."""
        if kind not in ("character", "variant", "voice"):
            raise ValueError(f"invalid registry kind {kind!r}")
        now = utcnow_iso()
        with self.db.transaction(immediate=True) as conn:
            latest = self._one(
                conn,
                "SELECT revision, status FROM registry_revisions"
                " WHERE entity_id = ? ORDER BY revision DESC LIMIT 1",
                (entity_id,),
            )
            next_rev = (int(latest["revision"]) + 1) if latest else 1
            if (
                expected_revision is not None
                and latest is not None
                and int(latest["revision"]) != expected_revision
            ):
                raise RevisionConflict(
                    f"entity {entity_id} at revision {latest['revision']}, "
                    f"expected {expected_revision}"
                )
            if latest and latest["status"] == "proposed":
                # one open proposal at a time: overwrite the unapproved draft
                conn.execute(
                    "UPDATE registry_revisions SET payload = ?, evidence_artifact_ids = ?,"
                    " updated_at = ? WHERE entity_id = ? AND revision = ?",
                    (_dumps(payload), _dumps(evidence_artifact_ids or []), now,
                     entity_id, latest["revision"]),
                )
                return {"entity_id": entity_id, "revision": int(latest["revision"]),
                        "status": "proposed"}
            conn.execute(
                "INSERT INTO registry_revisions (id, entity_id, kind, revision,"
                " parent_character_id, payload, status, evidence_artifact_ids,"
                " created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?)",
                (new_id(), entity_id, kind, next_rev, parent_character_id,
                 _dumps(payload), _dumps(evidence_artifact_ids or []), now, now),
            )
            return {"entity_id": entity_id, "revision": next_rev, "status": "proposed"}

    def approve_registry_revision(
        self, entity_id: str, revision: int, expected_review_revision: int | None = None
    ) -> dict:
        now = utcnow_iso()
        with self.db.transaction(immediate=True) as conn:
            row = self._one(
                conn,
                "SELECT * FROM registry_revisions WHERE entity_id = ? AND revision = ?",
                (entity_id, revision),
            )
            if row is None:
                raise NotFound(f"registry revision {entity_id}@{revision}")
            if row["status"] == "approved":
                return {"entity_id": entity_id, "revision": revision,
                        "status": "approved", "idempotent": True}
            if row["status"] == "rejected":
                raise InvalidTransition("rejected revision cannot be approved")
            if expected_review_revision is not None and row["payload"]:
                pass  # hook for review-revision CAS at the review layer
            conn.execute(
                "UPDATE registry_revisions SET status = 'approved', updated_at = ?"
                " WHERE entity_id = ? AND revision = ? AND status = 'proposed'",
                (now, entity_id, revision),
            )
            return {"entity_id": entity_id, "revision": revision,
                    "status": "approved", "idempotent": False}

    def reject_registry_revision(self, entity_id: str, revision: int) -> dict:
        now = utcnow_iso()
        with self.db.transaction(immediate=True) as conn:
            row = self._one(
                conn,
                "SELECT status FROM registry_revisions WHERE entity_id = ? AND revision = ?",
                (entity_id, revision),
            )
            if row is None:
                raise NotFound(f"registry revision {entity_id}@{revision}")
            if row["status"] == "approved":
                raise InvalidTransition("approved revisions are immutable; supersede instead")
            conn.execute(
                "UPDATE registry_revisions SET status = 'rejected', updated_at = ?"
                " WHERE entity_id = ? AND revision = ?",
                (now, entity_id, revision),
            )
            return {"entity_id": entity_id, "revision": revision, "status": "rejected"}

    def latest_registry(self, entity_id: str, status: str | None = None) -> dict | None:
        q = "SELECT * FROM registry_revisions WHERE entity_id = ?"
        params: list = [entity_id]
        if status:
            q += " AND status = ?"
            params.append(status)
        q += " ORDER BY revision DESC LIMIT 1"
        with self.db.transaction() as conn:
            row = self._one(conn, q, tuple(params))
        return dict(row) if row else None

    def registry_payload(self, entity_id: str, revision: int | None = None) -> dict:
        with self.db.transaction() as conn:
            if revision is None:
                row = self._one(
                    conn,
                    "SELECT * FROM registry_revisions WHERE entity_id = ?"
                    " ORDER BY revision DESC LIMIT 1",
                    (entity_id,),
                )
            else:
                row = self._one(
                    conn,
                    "SELECT * FROM registry_revisions WHERE entity_id = ? AND revision = ?",
                    (entity_id, revision),
                )
        if row is None:
            raise NotFound(f"registry revision {entity_id}@{revision}")
        return dict(row)

    # ═══ panel bindings (§14) ═════════════════════════════════════════════════

    def set_panel_bindings(
        self, panel_id: str, expected_revision: int | None, bindings: list[dict]
    ) -> dict:
        """Validate referenced revisions + approval status, then store one row
        per binding with an incremented binding_revision (§14.1)."""
        now = utcnow_iso()
        with self.db.transaction(immediate=True) as conn:
            panel = self._one(
                conn, "SELECT * FROM panels WHERE id = ? AND deleted = 0", (panel_id,)
            )
            if panel is None:
                raise NotFound(f"panel {panel_id!r}")
            latest = self._one(
                conn,
                "SELECT COALESCE(MAX(binding_revision), 0) AS max_rev"
                " FROM panel_bindings WHERE panel_id = ?",
                (panel_id,),
            )
            new_rev = int(latest["max_rev"] if latest else 0) + 1
            for b in bindings:
                for key, _table in (
                    ("character", "registry_revisions"),
                    ("variant", "registry_revisions"),
                ):
                    ent, rev = b.get(f"{key}_id"), b.get(f"{key}_revision")
                    if ent is None:
                        continue
                    r = self._one(
                        conn,
                        "SELECT status FROM registry_revisions"
                        " WHERE entity_id = ? AND revision = ?",
                        (ent, rev),
                    )
                    if r is None:
                        raise NotFound(f"{key} revision {ent}@{rev}")
                    if r["status"] != "approved":
                        raise BlockedPrerequisite(
                            f"{key} revision {ent}@{rev} is not approved "
                            f"(production requires approved bindings, D05)"
                        )
            conn.execute(
                "INSERT INTO panel_bindings (id, panel_id, binding_revision,"
                " character_id, character_revision, variant_id, variant_revision,"
                " line_bindings, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id(), panel_id, new_rev,
                    _first(bindings, "character_id"),
                    _first(bindings, "character_revision"),
                    _first(bindings, "variant_id"),
                    _first(bindings, "variant_revision"),
                    _dumps(bindings),
                    now, now,
                ),
            )
            return {"panel_id": panel_id, "binding_revision": new_rev}

    def latest_panel_bindings(self, panel_id: str) -> dict | None:
        with self.db.transaction() as conn:
            row = self._one(
                conn,
                "SELECT * FROM panel_bindings WHERE panel_id = ?"
                " ORDER BY binding_revision DESC LIMIT 1",
                (panel_id,),
            )
        return dict(row) if row else None

    # ═══ work / attempts (§9) ═════════════════════════════════════════════════

    def plan_work(self, specs: list[dict]) -> list[dict]:
        """Insert-or-return EXACT logical work (§5.4 plan_work).

        Each spec: {scope_key, stage, ratio, mode, input_fingerprint, desired_spec,
        series_id, chapter_id?, priority?, blocked_reason?}. Repeating an exact
        spec returns the existing work id (never a duplicate row)."""
        out: list[dict] = []
        now = utcnow_iso()
        with self.db.transaction(immediate=True) as conn:
            for spec in specs:
                fp = spec["input_fingerprint"]
                key = (
                    spec["scope_key"],
                    spec["stage"],
                    spec.get("ratio", ""),
                    spec.get("mode", "production"),
                    fp,
                )
                row = self._one(
                    conn,
                    "SELECT id, state, blocked_reason FROM work_items"
                    " WHERE scope_key = ? AND stage = ? AND ratio = ? AND mode = ?"
                    " AND input_fingerprint = ?",
                    key,
                )
                if row:
                    out.append({"work_id": row["id"], "state": row["state"],
                                "created": False, "blocked_reason": row["blocked_reason"]})
                    continue
                wid = new_id()
                state = "blocked" if spec.get("blocked_reason") else "ready"
                conn.execute(
                    "INSERT INTO work_items (id, series_id, chapter_id, scope_key, stage,"
                    " ratio, mode, input_fingerprint, desired_spec, state, priority,"
                    " blocked_reason, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        wid, spec["series_id"], spec.get("chapter_id"),
                        spec["scope_key"], spec["stage"], spec.get("ratio", ""),
                        spec.get("mode", "production"), fp,
                        _dumps(spec.get("desired_spec", {})),
                        state, int(spec.get("priority", 100)),
                        spec.get("blocked_reason", ""), now, now,
                    ),
                )
                out.append({"work_id": wid, "state": state, "created": True,
                            "blocked_reason": spec.get("blocked_reason", "")})
        return out

    def claim_attempt(self, work_id: str, owner_epoch: str) -> dict:
        """Atomic claim: READY→ACTIVE + allocate attempt 1..n (§9.2 step 1).

        At most one nonterminal attempt per work (partial unique index). A
        failed work item may claim again (bounded retry policy governs); a
        work with a live uncertain attempt is held (D06).
        """
        now = utcnow_iso()
        with self.db.transaction(immediate=True) as conn:
            work = self._one(conn, "SELECT * FROM work_items WHERE id = ?", (work_id,))
            if work is None:
                raise NotFound(f"work {work_id!r}")
            if work["state"] == "succeeded":
                raise InvalidTransition("succeeded work cannot claim a new attempt")
            if work["state"] == "blocked":
                raise BlockedPrerequisite(
                    work["blocked_reason"] or "work is blocked (no reason recorded)"
                )
            live = self._one(
                conn,
                "SELECT id, state, attempt_number FROM attempts WHERE work_id = ?"
                " AND state IN ('prepared','submitting','submission_uncertain',"
                "'running','downloading','validating')",
                (work_id,),
            )
            if live:
                if live["state"] == "submission_uncertain":
                    raise BlockedPrerequisite(
                        f"attempt #{live['attempt_number']} is submission_uncertain; "
                        "reconcile before any new submission (D06)"
                    )
                raise InvalidTransition(
                    f"attempt #{live['attempt_number']} ({live['state']}) still active"
                )
            if work["state"] == "active":
                # stale ACTIVE from a dead owner: adoption path must clear it
                # first (§9.1a recovery); a plain claim may not.
                raise InvalidTransition(
                    "work is ACTIVE from a previous owner; recover/adopt before claiming"
                )
            if not can_work_transition(WorkState(work["state"]), WorkState.ACTIVE):
                raise InvalidTransition(f"illegal work transition {work['state']} → active")
            num = int(self._one(
                conn, "SELECT COALESCE(MAX(attempt_number), 0) FROM attempts WHERE work_id = ?",
                (work_id,),
            )[0]) + 1
            aid = new_id()
            conn.execute(
                "INSERT INTO attempts (id, work_id, attempt_number, owner_epoch, state,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, 'prepared', ?, ?)",
                (aid, work_id, num, owner_epoch, now, now),
            )
            conn.execute(
                "UPDATE work_items SET state = 'active', updated_at = ? WHERE id = ?",
                (now, work_id),
            )
            return {"attempt_id": aid, "attempt_number": num, "work_state": "active"}

    def record_attempt_intent(
        self,
        attempt_id: str,
        owner_epoch: str,
        *,
        request_identity: str,
        seed: int | None,
        upload_plan: list[str],
        output_prefix: str,
        compiled_workflow_hash: str | None = None,
        compiled_workflow_artifact: str | None = None,
    ) -> None:
        """Persist immutable request intent BEFORE any submission side effect."""
        now = utcnow_iso()
        with self.db.transaction(immediate=True) as conn:
            row = self._require_attempt(conn, attempt_id)
            if row["owner_epoch"] != owner_epoch:
                raise RevisionConflict("attempt owner epoch mismatch (fencing)")
            if row["state"] not in ("prepared", "submitting"):
                raise InvalidTransition(f"attempt in state {row['state']}")
            conn.execute(
                "UPDATE attempts SET request_identity = ?, seed = ?, upload_plan = ?,"
                " output_prefix = ?, compiled_workflow_hash = ?,"
                " compiled_workflow_artifact = ?, updated_at = ? WHERE id = ?",
                (request_identity, seed, _dumps(upload_plan), output_prefix,
                 compiled_workflow_hash, compiled_workflow_artifact, now, attempt_id),
            )

    def begin_submission(self, attempt_id: str, owner_epoch: str) -> None:
        now = utcnow_iso()
        with self.db.transaction(immediate=True) as conn:
            row = self._require_attempt(conn, attempt_id)
            self._check_epoch(row, owner_epoch)
            self._transition_attempt(conn, row, AttemptState.SUBMITTING, now)

    def record_remote_acceptance(
        self, attempt_id: str, owner_epoch: str, prompt_id: str, server_identity: str
    ) -> None:
        """Persist prompt id/server identity + running IMMEDIATELY on accept (§9.2.4)."""
        now = utcnow_iso()
        with self.db.transaction(immediate=True) as conn:
            row = self._require_attempt(conn, attempt_id)
            self._check_epoch(row, owner_epoch)
            target = (
                AttemptState.RUNNING
                if row["state"] in ("submitting", "prepared", "submission_uncertain")
                else None
            )
            if row["state"] == "submission_uncertain":
                # reconciliation resolved it: re-activate the held work (D06)
                conn.execute(
                    "UPDATE attempts SET remote_prompt_id = ?, server_identity = ?,"
                    " state = 'running', submitted_at = COALESCE(submitted_at, ?),"
                    " updated_at = ? WHERE id = ?",
                    (prompt_id, server_identity, now, now, attempt_id),
                )
                conn.execute(
                    "UPDATE work_items SET state = 'active', blocked_reason = '',"
                    " updated_at = ? WHERE id = ? AND state = 'blocked'",
                    (now, row["work_id"]),
                )
                return
            if target is None:
                raise InvalidTransition(
                    f"cannot record acceptance from state {row['state']}"
                )
            conn.execute(
                "UPDATE attempts SET remote_prompt_id = ?, server_identity = ?,"
                " state = ?, submitted_at = COALESCE(submitted_at, ?), updated_at = ?"
                " WHERE id = ?",
                (prompt_id, server_identity, target.value, now, now, attempt_id),
            )

    def mark_submission_uncertain(self, attempt_id: str, owner_epoch: str, detail: str) -> None:
        now = utcnow_iso()
        with self.db.transaction(immediate=True) as conn:
            row = self._require_attempt(conn, attempt_id)
            self._check_epoch(row, owner_epoch)
            self._transition_attempt(
                conn, row, AttemptState.SUBMISSION_UNCERTAIN, now,
                error_kind="submission_uncertain", error_detail=detail,
            )
            conn.execute(
                "UPDATE work_items SET state = 'blocked',"
                " blocked_reason = 'submission_uncertain: reconcile attempt " + attempt_id + "',"
                " updated_at = ? WHERE id = ?",
                (now, row["work_id"]),
            )

    def record_attempt_outcome(
        self,
        attempt_id: str,
        owner_epoch: str,
        outcome: AttemptState | str,
        *,
        error_kind: str | None = None,
        error_detail: str | None = None,
    ) -> dict:
        """Typed outcome write with transition validation (§9.1a).

        Returns {attempt_state, work_state}. Does NOT promote artifacts —
        that is promote_artifacts with its own validation.
        """
        now = utcnow_iso()
        outcome = AttemptState(outcome)
        with self.db.transaction(immediate=True) as conn:
            row = self._require_attempt(conn, attempt_id)
            self._check_epoch(row, owner_epoch)
            self._transition_attempt(conn, row, outcome, now,
                                     error_kind=error_kind, error_detail=error_detail)
            work_next: str | None = None
            blocked_reason = ""
            if outcome is AttemptState.SUCCEEDED:
                work_next = None  # promotion moves the work (promote_artifacts)
            elif outcome in (AttemptState.FAILED, AttemptState.INTERRUPTED, AttemptState.STALE):
                work_next = "failed" if outcome is AttemptState.FAILED else "blocked"
                if outcome is AttemptState.STALE:
                    blocked_reason = "result stale (superseded by a newer desired fingerprint)"
                elif outcome is AttemptState.INTERRUPTED:
                    blocked_reason = f"interrupted: {error_detail or ''}".strip()
            elif outcome is AttemptState.SUBMISSION_UNCERTAIN:
                work_next = "blocked"
                blocked_reason = "submission_uncertain: reconcile"
            if work_next:
                conn.execute(
                    "UPDATE work_items SET state = ?, blocked_reason = ?, updated_at = ?"
                    " WHERE id = ?",
                    (work_next, blocked_reason, now, row["work_id"]),
                )
            return {"attempt_state": outcome.value, "work_state": work_next or "active"}

    def _require_attempt(self, conn: sqlite3.Connection, attempt_id: str) -> sqlite3.Row:
        row = self._one(conn, "SELECT * FROM attempts WHERE id = ?", (attempt_id,))
        if row is None:
            raise NotFound(f"attempt {attempt_id!r}")
        return row

    @staticmethod
    def _check_epoch(row: sqlite3.Row, owner_epoch: str) -> None:
        if row["owner_epoch"] != owner_epoch:
            raise RevisionConflict(
                f"attempt owned by epoch {row['owner_epoch']}, not {owner_epoch} (fencing)"
            )

    def _transition_attempt(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        target: AttemptState,
        now: str,
        *,
        error_kind: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        current = AttemptState(row["state"])
        if not can_attempt_transition(current, target):
            raise InvalidTransition(f"illegal attempt transition {current.value} → {target.value}")
        sets = ["state = ?", "updated_at = ?"]
        params: list[Any] = [target.value, now]
        if error_kind is not None:
            sets.append("error_kind = ?")
            params.append(error_kind)
        if error_detail is not None:
            sets.append("error_detail = ?")
            params.append(error_detail)
        if target in (AttemptState.SUCCEEDED, AttemptState.FAILED, AttemptState.INTERRUPTED,
                      AttemptState.STALE):
            sets.append("completed_at = ?")
            params.append(now)
        params.append(row["id"])
        conn.execute(f"UPDATE attempts SET {', '.join(sets)} WHERE id = ?", tuple(params))
        if target in (AttemptState.FAILED, AttemptState.INTERRUPTED, AttemptState.STALE):
            conn.execute(
                "UPDATE work_items SET state = 'failed', updated_at = ? WHERE id = ?"
                " AND state = 'active'",
                (now, row["work_id"]),
            )

    # ── recovery adoption (§9.1a) ─────────────────────────────────────────────
    def adopt_nonterminal_attempts(self, previous_epoch: str, new_epoch: str) -> list[str]:
        """After owner death: adopt nonterminal attempts in a short transaction.

        Records the new owner epoch (fencing); does NOT allocate a new seed or
        alter the immutable request intent. The old owner's callbacks can no
        longer promote. Returns adopted attempt ids.
        """
        now = utcnow_iso()
        adopted: list[str] = []
        with self.db.transaction(immediate=True) as conn:
            rows = conn.execute(
                "SELECT id FROM attempts WHERE owner_epoch = ? AND state IN"
                " ('prepared','submitting','submission_uncertain','running',"
                "'downloading','validating')",
                (previous_epoch,),
            ).fetchall()
            for r in rows:
                conn.execute(
                    "UPDATE attempts SET owner_epoch = ?, updated_at = ? WHERE id = ?",
                    (new_epoch, now, r["id"]),
                )
                adopted.append(r["id"])
        return adopted

    # ── artifacts + promotion (§6) ───────────────────────────────────────────
    def promote_artifacts(
        self,
        attempt_id: str,
        owner_epoch: str,
        expected_fingerprint: str,
        validated_artifacts: list[dict],
    ) -> dict:
        """Transactional promotion (§6.2).

        Verify: attempt epoch, work fingerprint, attempt success. Then record
        artifact rows + dependencies and swap current-output pointers. A stale
        result is retained historically, never promoted.
        """
        now = utcnow_iso()
        with self.db.transaction(immediate=True) as conn:
            attempt = self._one(conn, "SELECT * FROM attempts WHERE id = ?", (attempt_id,))
            if attempt is None:
                raise NotFound(f"attempt {attempt_id!r}")
            if attempt["owner_epoch"] != owner_epoch:
                raise RevisionConflict("attempt owner epoch mismatch (fencing)")
            work = self._one(conn, "SELECT * FROM work_items WHERE id = ?", (attempt["work_id"],))
            if work is None:
                raise NotFound("work for attempt")
            if work["input_fingerprint"] != expected_fingerprint:
                raise InvalidTransition(
                    "work fingerprint changed since this attempt started; "
                    "result is historical and cannot promote (stale-result fencing)"
                )
            if work["state"] not in ("active", "succeeded"):
                raise InvalidTransition(f"work in state {work['state']} cannot promote")
            spec = json.loads(work["desired_spec"])
            if spec.get("shot_revision_id"):
                from .shot_store import ShotStore

                ShotStore(self.db).require_production_ready(
                    spec["shot_revision_id"], work["ratio"], conn=conn
                )
            artifact_ids: list[str] = []
            for art in validated_artifacts:
                aid = new_id()
                artifact_ids.append(aid)
                conn.execute(
                    "INSERT INTO artifacts (id, series_id, scope_key, kind, mode,"
                    " producing_attempt_id, deterministic_operation_id, relative_path,"
                    " sha256, size, input_fingerprint, media_metadata, result_metadata,"
                    " validation_status, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'validated', ?)",
                    (
                        aid,
                        work["series_id"],
                        work["scope_key"],
                        art["kind"],
                        work["mode"],
                        attempt_id,
                        art.get("deterministic_operation_id"),
                        art["relative_path"],
                        art["sha256"],
                        int(art["size"]),
                        work["input_fingerprint"],
                        _dumps(art.get("media_metadata", {})),
                        _dumps(art.get("result_metadata", {})),
                        now,
                    ),
                )
                for dep in art.get("dependencies", []):
                    conn.execute(
                        "INSERT INTO artifact_dependencies (artifact_id, input_artifact_id,"
                        " semantic_role, created_at) VALUES (?, ?, ?, ?)",
                        (aid, dep["input_artifact_id"], dep.get("semantic_role", "input"), now),
                    )
                conn.execute(
                    "INSERT INTO current_outputs (scope_key, stage, ratio, mode, output_role,"
                    " artifact_id, input_fingerprint, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(scope_key, stage, ratio, mode, output_role) DO UPDATE SET"
                    " artifact_id = excluded.artifact_id,"
                    " input_fingerprint = excluded.input_fingerprint,"
                    " updated_at = excluded.updated_at",
                    (
                        work["scope_key"],
                        work["stage"],
                        work["ratio"],
                        work["mode"],
                        art.get("output_role", art["kind"]),
                        aid,
                        work["input_fingerprint"],
                        now,
                    ),
                )
            if spec.get("shot_revision_id"):
                from ..motion_review import MotionReviewService

                for aid, art in zip(artifact_ids, validated_artifacts, strict=True):
                    if art["kind"] == "motion":
                        MotionReviewService(self.db).register_candidate(
                            shot_revision_id=spec["shot_revision_id"],
                            series_id=work["series_id"],
                            ratio=work["ratio"],
                            artifact_id=aid,
                            input_fingerprint=work["input_fingerprint"],
                            conn=conn,
                        )
            # attempt terminal success + work success, one transaction
            conn.execute(
                "UPDATE attempts SET state = 'succeeded', completed_at = ?, updated_at = ?"
                " WHERE id = ?",
                (now, now, attempt_id),
            )
            conn.execute(
                "UPDATE work_items SET state = 'succeeded', blocked_reason = '',"
                " updated_at = ? WHERE id = ?",
                (now, work["id"]),
            )
            return {
                "artifact_ids": artifact_ids,
                "work_id": work["id"],
                "input_fingerprint": work["input_fingerprint"],
            }

    def current_output(
        self, scope_key: str, stage: str, ratio: str = "", mode: str = "production",
        output_role: str | None = None,
    ) -> dict | None:
        with self.db.transaction() as conn:
            if output_role is None:
                row = self._one(
                    conn,
                    "SELECT * FROM current_outputs WHERE scope_key = ? AND stage = ?"
                    " AND ratio = ? AND mode = ?",
                    (scope_key, stage, ratio, mode),
                )
            else:
                row = self._one(
                    conn,
                    "SELECT * FROM current_outputs WHERE scope_key = ? AND stage = ?"
                    " AND ratio = ? AND mode = ? AND output_role = ?",
                    (scope_key, stage, ratio, mode, output_role),
                )
        return dict(row) if row else None

    def find_work(
        self,
        scope_key: str | None = None,
        stage: str | None = None,
        ratio: str | None = None,
        mode: str = "production",
        fingerprint: str | None = None,
        state: str | None = None,
    ) -> list[dict]:
        q = "SELECT * FROM work_items WHERE mode = ?"
        params: list[Any] = [mode]
        if scope_key:
            q += " AND scope_key = ?"
            params.append(scope_key)
        if stage:
            q += " AND stage = ?"
            params.append(stage)
        if ratio is not None:
            q += " AND ratio = ?"
            params.append(ratio)
        if fingerprint:
            q += " AND input_fingerprint = ?"
            params.append(fingerprint)
        if state:
            q += " AND state = ?"
            params.append(state)
        with self.db.transaction() as conn:
            return [dict(r) for r in conn.execute(q + " ORDER BY priority, created_at",
                                                   tuple(params)).fetchall()]

    def get_work(self, work_id: str) -> dict:
        with self.db.transaction() as conn:
            row = self._one(conn, "SELECT * FROM work_items WHERE id = ?", (work_id,))
        if row is None:
            raise NotFound(f"work {work_id!r}")
        return dict(row)

    def get_attempt(self, attempt_id: str) -> dict:
        with self.db.transaction() as conn:
            row = self._one(conn, "SELECT * FROM attempts WHERE id = ?", (attempt_id,))
        if row is None:
            raise NotFound(f"attempt {attempt_id!r}")
        return dict(row)

    def nonterminal_attempts(self, previous_epoch: str | None = None) -> list[dict]:
        q = ("SELECT * FROM attempts WHERE state IN"
             " ('prepared','submitting','submission_uncertain'"
             ",'running','downloading','validating')")
        params: tuple = ()
        if previous_epoch is not None:
            q += " AND owner_epoch = ?"
            params = (previous_epoch,)
        with self.db.transaction() as conn:
            return [dict(r) for r in conn.execute(q, params).fetchall()]

    def set_work_state(
        self, work_id: str, target: WorkState | str, *, blocked_reason: str = ""
    ) -> str:
        with self.db.transaction(immediate=True) as conn:
            row = self._one(conn, "SELECT state FROM work_items WHERE id = ?", (work_id,))
            if row is None:
                raise NotFound(f"work {work_id!r}")
            if not can_work_transition(WorkState(row["state"]), WorkState(target)):
                raise InvalidTransition(
                    f"illegal work transition {row['state']} → {target}"
                )
            conn.execute(
                "UPDATE work_items SET state = ?, blocked_reason = ?, updated_at = ?"
                " WHERE id = ?",
                (WorkState(target).value, blocked_reason, utcnow_iso(), work_id),
            )
            return WorkState(target).value

    # ═══ runs (§8.1) ══════════════════════════════════════════════════════════

    def create_run(
        self,
        slug: str,
        chapter_number: int | None,
        requested_ratios: list[str],
        mode: str = "production",
        config_snapshot: dict | None = None,
    ) -> dict:
        now = utcnow_iso()
        rid = new_id()
        with self.db.transaction(immediate=True) as conn:
            series = self._one(conn, "SELECT id FROM series WHERE slug = ?", (slug,))
            if series is None:
                raise NotFound(f"series {slug!r}")
            conn.execute(
                "INSERT INTO runs (id, series_id, chapter_number, requested_ratios, mode,"
                " config_snapshot, config_hash, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'created', ?, ?)",
                (
                    rid, series["id"], chapter_number, _dumps(requested_ratios), mode,
                    _dumps(config_snapshot or {}), canonical_hash(config_snapshot or {}),
                    now, now,
                ),
            )
        return {
            "run_id": rid, "status": "created",
            "config_hash": canonical_hash(config_snapshot or {}),
        }

    def get_run(self, run_id: str) -> dict:
        with self.db.transaction() as conn:
            row = self._one(conn, "SELECT * FROM runs WHERE id = ?", (run_id,))
        if row is None:
            raise NotFound(f"run {run_id!r}")
        return dict(row)

    def update_run(self, run_id: str, **fields: Any) -> dict:
        allowed = {
            "status", "requested_control", "resources_state", "resources_observed_at",
            "last_summary",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"immutable/unknown run fields: {sorted(unknown)}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        params = [
            _dumps(v) if k == "last_summary" else v for k, v in fields.items()
        ]
        with self.db.transaction(immediate=True) as conn:
            if self._one(conn, "SELECT 1 FROM runs WHERE id = ?", (run_id,)) is None:
                raise NotFound(f"run {run_id!r}")
            conn.execute(
                f"UPDATE runs SET {sets}, updated_at = ? WHERE id = ?",
                (*params, utcnow_iso(), run_id),
            )
        return self.get_run(run_id)

    def associate_work(
        self, run_id: str, work_ids: list[str], output_roles: list[str] | None = None
    ) -> None:
        now = utcnow_iso()
        with self.db.transaction(immediate=True) as conn:
            for wid in work_ids:
                conn.execute(
                    "INSERT INTO run_work (run_id, work_id, output_roles, created_at)"
                    " VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    (run_id, wid, _dumps(output_roles or []), now),
                )

    def run_work_ids(self, run_id: str) -> list[str]:
        with self.db.transaction() as conn:
            return [r[0] for r in conn.execute(
                "SELECT work_id FROM run_work WHERE run_id = ?", (run_id,)
            ).fetchall()]

    # ═══ progress derivation (§20.2) ═════════════════════════════════════════

    def derive_progress(self, slug: str, chapter_number: int | None = None,
                        ratios: list[str] | None = None) -> dict:
        """Desired vs complete per stage/ratio from current_outputs + work.

        Never derived from the highest historical enum (§10).
        """
        with self.db.transaction() as conn:
            series = self._one(conn, "SELECT id, required_ratios FROM series WHERE slug = ?",
                               (slug,))
            if series is None:
                raise NotFound(f"series {slug!r}")
            required = json.loads(series["required_ratios"])
            q = (
                "SELECT w.stage, w.ratio, w.mode, w.state, COUNT(*) AS n"
                " FROM work_items w WHERE w.series_id = ?"
            )
            params: list[Any] = [series["id"]]
            if chapter_number is not None:
                q += (" AND w.chapter_id = (SELECT id FROM chapters"
                      " WHERE series_id = ? AND number = ?)")
                params += [series["id"], chapter_number]
            q += " GROUP BY w.stage, w.ratio, w.mode, w.state"
            by_stage: dict[str, dict[str, dict[str, int]]] = {}
            for r in conn.execute(q, tuple(params)).fetchall():
                stage = by_stage.setdefault(r["stage"], {})
                by_ratio = stage.setdefault(r["ratio"] or "shared", {})
                by_mode = by_ratio  # flattened; mode kept in the row
                by_mode[r["state"]] = by_mode.get(r["state"], 0) + int(r["n"])
            outs = conn.execute(
                "SELECT co.stage, co.ratio, co.mode, COUNT(*) AS n FROM current_outputs co"
                " JOIN artifacts a ON a.id = co.artifact_id"
                " JOIN work_items w ON w.scope_key = co.scope_key AND w.stage = co.stage"
                " AND w.input_fingerprint = co.input_fingerprint"
                " WHERE w.series_id = ? GROUP BY co.stage, co.ratio, co.mode",
                (series["id"],),
            ).fetchall()
            accepted: dict[str, dict[str, int]] = {}
            for r in outs:
                accepted.setdefault(r["stage"], {})[r["ratio"] or "shared"] = int(r["n"])
        return {
            "required_ratios": required,
            "selected_ratios": ratios or required,
            "work_by_stage": by_stage,
            "accepted_by_stage": accepted,
        }

    # ═══ command owner + control (§7; transport in scheduler/ownership.py) ════

    def read_command_owner(self) -> dict | None:
        with self.db.transaction() as conn:
            row = self._one(conn, "SELECT * FROM command_owner WHERE id = 1")
        return dict(row) if row else None

    def write_command_owner(self, owner: dict) -> None:
        with self.db.transaction(immediate=True) as conn:
            conn.execute("DELETE FROM command_owner WHERE id = 1")
            conn.execute(
                "INSERT INTO command_owner (id, owner_uuid, pid, process_identity, host_id,"
                " epoch, command_id, run_id, heartbeat_at, control_note)"
                " VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    owner["owner_uuid"], int(owner["pid"]),
                    owner.get("process_identity", ""), owner.get("host_id", ""),
                    owner["epoch"], owner.get("command_id"), owner.get("run_id"),
                    owner.get("heartbeat_at", utcnow_iso()),
                    owner.get("control_note", ""),
                ),
            )

    def clear_command_owner(self, owner_uuid: str) -> None:
        with self.db.transaction(immediate=True) as conn:
            conn.execute("DELETE FROM command_owner WHERE id = 1 AND owner_uuid = ?",
                         (owner_uuid,))

    def request_control(
        self, target_owner_uuid: str, target_epoch: str, action: str,
        operation_id: str, approval_record: dict | None = None,
    ) -> dict:
        """Idempotent control request (§7.2). Same op id returns original row."""
        now = utcnow_iso()
        with self.db.transaction(immediate=True) as conn:
            prior = self._one(
                conn, "SELECT * FROM control_requests WHERE id = ?", (operation_id,)
            )
            if prior is not None:
                return dict(prior)
            conn.execute(
                "INSERT INTO control_requests (id, target_owner_uuid, target_epoch, action,"
                " approval_record, state, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 'requested', ?, ?)",
                (operation_id, target_owner_uuid, target_epoch, action,
                 _dumps(approval_record) if approval_record else None, now, now),
            )
            row = self._one(conn, "SELECT * FROM control_requests WHERE id = ?", (operation_id,))
            return dict(row)

    def pending_control_requests(self, owner_uuid: str, epoch: str) -> list[dict]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM control_requests WHERE target_owner_uuid = ?"
                " AND target_epoch = ? AND state = 'requested'",
                (owner_uuid, epoch),
            ).fetchall()
        return [dict(r) for r in rows]

    def acknowledge_control(self, request_id: str, owner_uuid: str, epoch: str) -> None:
        with self.db.transaction(immediate=True) as conn:
            row = self._one(conn, "SELECT * FROM control_requests WHERE id = ?", (request_id,))
            if row is None:
                raise NotFound(f"control request {request_id!r}")
            if row["target_owner_uuid"] != owner_uuid or row["target_epoch"] != epoch:
                raise RevisionConflict("control request targets a different owner/epoch")
            conn.execute(
                "UPDATE control_requests SET state = 'acknowledged', updated_at = ?"
                " WHERE id = ?",
                (utcnow_iso(), request_id),
            )

    def complete_control(self, request_id: str, response: dict | None = None) -> None:
        with self.db.transaction(immediate=True) as conn:
            row = self._one(conn, "SELECT * FROM control_requests WHERE id = ?", (request_id,))
            if row is None:
                raise NotFound(f"control request {request_id!r}")
            if row["state"] == "completed":
                return  # idempotent
            if row["state"] not in ("acknowledged", "requested"):
                raise InvalidTransition(f"control request in state {row['state']}")
            conn.execute(
                "UPDATE control_requests SET state = 'completed', response_payload = ?,"
                " updated_at = ? WHERE id = ?",
                (_dumps(response) if response else None, utcnow_iso(), request_id),
            )


def _first(bindings: list[dict], key: str) -> Any:
    for b in bindings:
        if b.get(key) is not None:
            return b[key]
    return None


__all__ = ["KomaStore"]

