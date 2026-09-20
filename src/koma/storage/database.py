"""SQLite connection policy, schema and migrations (PLAN.md §5).

SQLite is the SOLE mutable metadata authority. Ordinary files hold media and
immutable JSON artifacts; exported JSON can never write back implicitly.

Connection rules (§5.1):
- foreign keys ON every connection, WAL journaling, bounded busy timeout,
  durable commits (synchronous=FULL).
- one connection per thread (check_same_thread=True, the sqlite default).
- all read/check/write operations are one SHORT transaction; claims and
  revisioned mutations use BEGIN IMMEDIATE; never hold a transaction while
  calling a model, uploading, downloading, hashing a large file, decoding
  media, waiting for a process, or asking the user a question.

Schema version + ordered migrations. Unknown nonempty legacy stores refuse to
initialize (D01): no silent delete or overwrite of unrecognized data.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..log import get_logger

log = get_logger("storage.database")

SCHEMA_VERSION = 3
DB_NAME = "koma.sqlite3"
_BUSY_TIMEOUT_MS = 5000

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS series (
    id                TEXT PRIMARY KEY,
    slug              TEXT NOT NULL UNIQUE,
    revision          INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    title             TEXT NOT NULL DEFAULT '',
    language          TEXT NOT NULL DEFAULT 'ja',
    voice_language    TEXT NOT NULL DEFAULT 'ja',
    required_ratios   TEXT NOT NULL,           -- JSON list, e.g. ["16:9","9:16"]
    subtitle_languages TEXT NOT NULL,          -- JSON list, e.g. ["en","zh-HANS"]
    video_profile_id  TEXT,
    production_settings TEXT NOT NULL DEFAULT '{}',  -- JSON payload
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chapters (
    id            TEXT PRIMARY KEY,
    series_id     TEXT NOT NULL REFERENCES series(id),
    number        INTEGER NOT NULL CHECK (number > 0),
    revision      INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
    title         TEXT NOT NULL DEFAULT '',
    requested_scope TEXT NOT NULL DEFAULT '{}',  -- JSON payload
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (series_id, number)
);

CREATE TABLE IF NOT EXISTS pages (
    id               TEXT PRIMARY KEY,
    chapter_id       TEXT NOT NULL REFERENCES chapters(id),
    series_id        TEXT NOT NULL REFERENCES series(id),
    birth_number     INTEGER NOT NULL CHECK (birth_number > 0),
    sequence_position INTEGER NOT NULL CHECK (sequence_position > 0),
    source_artifact_id TEXT,
    source_revision  INTEGER NOT NULL DEFAULT 1 CHECK (source_revision > 0),
    source_path      TEXT NOT NULL DEFAULT '',
    source_sha256    TEXT NOT NULL DEFAULT '',
    width            INTEGER NOT NULL DEFAULT 0 CHECK (width >= 0),
    height           INTEGER NOT NULL DEFAULT 0 CHECK (height >= 0),
    direction        TEXT NOT NULL DEFAULT 'rtl',
    direction_confidence REAL NOT NULL DEFAULT 1.0 CHECK (direction_confidence >= 0),
    order_review_revision INTEGER NOT NULL DEFAULT 0,
    order_review_status TEXT NOT NULL DEFAULT 'none',
    deleted          INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)),
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE (chapter_id, sequence_position)
);

CREATE INDEX IF NOT EXISTS idx_pages_series ON pages(series_id);

CREATE TABLE IF NOT EXISTS panels (
    id            TEXT PRIMARY KEY,             -- immutable panel id (§5.2)
    page_id       TEXT NOT NULL REFERENCES pages(id),
    series_id     TEXT NOT NULL REFERENCES series(id),
    chapter_id    TEXT NOT NULL REFERENCES chapters(id),
    birth_ordinal INTEGER NOT NULL CHECK (birth_ordinal > 0),
    bbox_x INTEGER NOT NULL CHECK (bbox_x >= 0),
    bbox_y INTEGER NOT NULL CHECK (bbox_y >= 0),
    bbox_w INTEGER NOT NULL CHECK (bbox_w > 0),
    bbox_h INTEGER NOT NULL CHECK (bbox_h > 0),
    crop_revision INTEGER NOT NULL DEFAULT 1 CHECK (crop_revision > 0),
    reading_order INTEGER NOT NULL CHECK (reading_order > 0),
    deleted       INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_panels_page ON panels(page_id);
CREATE INDEX IF NOT EXISTS idx_panels_chapter ON panels(chapter_id);

CREATE TABLE IF NOT EXISTS import_entries (
    id               TEXT PRIMARY KEY,
    chapter_id       TEXT NOT NULL REFERENCES chapters(id),
    import_source_id TEXT NOT NULL,
    page_id          TEXT NOT NULL REFERENCES pages(id),
    occurrence_key   TEXT NOT NULL,            -- file path or PDF page ordinal
    source_sha256    TEXT NOT NULL,
    operation_id     TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE (chapter_id, import_source_id, occurrence_key)
);

CREATE TABLE IF NOT EXISTS registry_revisions (
    id              TEXT PRIMARY KEY,
    entity_id       TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('character', 'variant', 'voice')),
    revision        INTEGER NOT NULL CHECK (revision > 0),
    parent_character_id TEXT,                  -- set for variant/voice rows
    payload         TEXT NOT NULL,             -- versioned JSON (validated on read)
    status          TEXT NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed', 'approved', 'rejected')),
    evidence_artifact_ids TEXT NOT NULL DEFAULT '[]',  -- JSON list
    approved_review_id TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (entity_id, revision)
);

CREATE TABLE IF NOT EXISTS panel_bindings (
    id              TEXT PRIMARY KEY,
    panel_id        TEXT NOT NULL REFERENCES panels(id),
    binding_revision INTEGER NOT NULL CHECK (binding_revision > 0),
    character_id    TEXT,
    character_revision INTEGER,
    variant_id      TEXT,
    variant_revision INTEGER,
    line_bindings   TEXT NOT NULL DEFAULT '[]',   -- JSON: [{line_id, voice_entity_revision}]
    review_id       TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bindings_panel ON panel_bindings(panel_id);

CREATE TABLE IF NOT EXISTS reviews (
    id              TEXT PRIMARY KEY,
    series_id       TEXT NOT NULL REFERENCES series(id),
    scope_key       TEXT NOT NULL,
    kind            TEXT NOT NULL,
    expected_aggregate_revision INTEGER NOT NULL,
    evidence_manifest TEXT NOT NULL,             -- JSON payload
    proposed_desired_state TEXT NOT NULL,        -- JSON payload
    final_decision  TEXT,                        -- JSON payload when decided
    decision_status TEXT NOT NULL DEFAULT 'open'
                        CHECK (decision_status IN ('open', 'approved', 'rejected', 'conflict')),
    actor           TEXT NOT NULL DEFAULT '',
    operation_id    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_operation
    ON reviews(operation_id) WHERE operation_id IS NOT NULL AND operation_id <> '';

CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    series_id       TEXT NOT NULL REFERENCES series(id),
    chapter_number  INTEGER,
    requested_ratios TEXT NOT NULL,              -- JSON list
    mode            TEXT NOT NULL DEFAULT 'production'
                        CHECK (mode IN ('production', 'preview')),
    config_snapshot TEXT NOT NULL,               -- JSON: frozen resolved settings
    config_hash     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'created'
                        CHECK (status IN ('created', 'running', 'pausing', 'paused',
                                          'waiting_review', 'waiting_service', 'held',
                                          'interrupted', 'failed', 'complete', 'cancelled')),
    requested_control TEXT,
    resources_state TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (resources_state IN ('active', 'draining', 'releasing',
                                                   'released', 'release_failed', 'unknown')),
    resources_observed_at TEXT,
    last_summary    TEXT,                        -- JSON payload
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_work (
    run_id        TEXT NOT NULL REFERENCES runs(id),
    work_id       TEXT NOT NULL REFERENCES work_items(id),
    output_roles  TEXT NOT NULL DEFAULT '[]',   -- JSON list
    created_at    TEXT NOT NULL,
    UNIQUE (run_id, work_id)
);

CREATE TABLE IF NOT EXISTS work_items (
    id              TEXT PRIMARY KEY,
    series_id       TEXT NOT NULL REFERENCES series(id),
    chapter_id      TEXT REFERENCES chapters(id),
    scope_key       TEXT NOT NULL,              -- series/chapter/page/panel/line/segment
    stage           TEXT NOT NULL,              -- colorize|keyframe|render|tts|upscale|...
    ratio           TEXT NOT NULL DEFAULT '',   -- '' = shared work (never SQL NULL)
    mode            TEXT NOT NULL DEFAULT 'production'
                        CHECK (mode IN ('production', 'preview')),
    input_fingerprint TEXT NOT NULL,            -- SHA-256 of canonical typed manifest
    desired_spec    TEXT NOT NULL,              -- JSON, validated on read
    state           TEXT NOT NULL DEFAULT 'blocked'
                        CHECK (state IN ('blocked', 'ready', 'active', 'succeeded',
                                         'failed', 'cancelled', 'superseded')),
    priority        INTEGER NOT NULL DEFAULT 100,
    blocked_reason  TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (scope_key, stage, ratio, mode, input_fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_work_state ON work_items(state);

CREATE TABLE IF NOT EXISTS attempts (
    id              TEXT PRIMARY KEY,
    work_id         TEXT NOT NULL REFERENCES work_items(id),
    attempt_number  INTEGER NOT NULL CHECK (attempt_number > 0),
    owner_epoch     TEXT NOT NULL,
    state           TEXT NOT NULL
                        CHECK (state IN ('prepared', 'submitting', 'submission_uncertain',
                                         'running', 'downloading', 'validating',
                                         'succeeded', 'failed', 'interrupted', 'stale')),
    request_identity TEXT NOT NULL DEFAULT '',   -- canonical JSON of the request intent
    seed            INTEGER,
    compiled_workflow_artifact TEXT,
    compiled_workflow_hash TEXT,
    upload_plan     TEXT NOT NULL DEFAULT '[]', -- JSON: stable upload names
    output_prefix   TEXT NOT NULL DEFAULT '',
    remote_prompt_id TEXT,
    server_identity TEXT,
    error_kind      TEXT,
    error_detail    TEXT,
    submitted_at    TEXT,
    completed_at    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (work_id, attempt_number)
);

-- At most one nonterminal attempt per work (§5.3: partial unique index).
CREATE UNIQUE INDEX IF NOT EXISTS idx_attempts_one_active
    ON attempts(work_id) WHERE state IN
    ('prepared', 'submitting', 'submission_uncertain', 'running', 'downloading', 'validating');

CREATE INDEX IF NOT EXISTS idx_attempts_remote ON attempts(remote_prompt_id)
    WHERE remote_prompt_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS artifacts (
    id              TEXT PRIMARY KEY,
    series_id       TEXT NOT NULL REFERENCES series(id),
    scope_key       TEXT NOT NULL,
    kind            TEXT NOT NULL,              -- color_master|keyframe|clip|mix|subtitle|...
    mode            TEXT NOT NULL DEFAULT 'production'
                        CHECK (mode IN ('production', 'preview')),
    producing_attempt_id TEXT REFERENCES attempts(id),
    deterministic_operation_id TEXT,
    relative_path   TEXT NOT NULL UNIQUE,       -- immutable artifact directory
    sha256          TEXT NOT NULL,
    size            INTEGER NOT NULL CHECK (size >= 0),
    input_fingerprint TEXT NOT NULL,
    media_metadata  TEXT NOT NULL DEFAULT '{}', -- typed JSON (media metadata)
    result_metadata TEXT NOT NULL DEFAULT '{}', -- typed JSON (per-stage result data)
    validation_status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (validation_status IN ('pending', 'validated', 'rejected')),
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_artifacts_scope ON artifacts(scope_key, kind);

CREATE TABLE IF NOT EXISTS artifact_dependencies (
    artifact_id    TEXT NOT NULL REFERENCES artifacts(id),
    input_artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    semantic_role  TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (artifact_id, input_artifact_id)
);

CREATE TABLE IF NOT EXISTS current_outputs (
    scope_key     TEXT NOT NULL,
    stage         TEXT NOT NULL,
    ratio         TEXT NOT NULL DEFAULT '',
    mode          TEXT NOT NULL DEFAULT 'production',
    output_role   TEXT NOT NULL,
    artifact_id   TEXT NOT NULL REFERENCES artifacts(id),
    input_fingerprint TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (scope_key, stage, ratio, mode, output_role)
);

CREATE TABLE IF NOT EXISTS profile_qualifications (
    profile_id       TEXT NOT NULL,
    workflow_id      TEXT NOT NULL,
    component_identity TEXT NOT NULL,            -- JSON: hashes/versions tuple
    server_fingerprint TEXT NOT NULL,
    captured_schema_hash TEXT NOT NULL,
    probe_artifacts  TEXT NOT NULL DEFAULT '[]', -- JSON list
    measured_metadata TEXT NOT NULL DEFAULT '{}', -- JSON payload
    status           TEXT NOT NULL DEFAULT 'unqualified'
                        CHECK (status IN ('qualified', 'failed', 'unqualified')),
    reason           TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (profile_id, workflow_id)
);

CREATE TABLE IF NOT EXISTS command_owner (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    owner_uuid    TEXT NOT NULL,
    pid           INTEGER NOT NULL,
    process_identity TEXT NOT NULL DEFAULT '',
    host_id       TEXT NOT NULL DEFAULT '',
    epoch         TEXT NOT NULL,
    command_id    TEXT,
    run_id        TEXT,
    heartbeat_at  TEXT NOT NULL,
    control_note  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS control_requests (
    id              TEXT PRIMARY KEY,
    target_owner_uuid TEXT NOT NULL,
    target_epoch    TEXT NOT NULL,
    action          TEXT NOT NULL,
    approval_record TEXT,                        -- JSON payload when approval required
    state           TEXT NOT NULL DEFAULT 'requested'
                        CHECK (state IN ('requested', 'acknowledged', 'completed', 'rejected',
                                         'expired')),
    response_payload TEXT,                       -- JSON payload
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# ── v2 migration (R1): versioned assets, story beats, shot plans, candidates ──
# Additive only; no changes to existing v1 tables (docs/r0-compat-map.md).
_MIGRATION_V2 = """
CREATE TABLE IF NOT EXISTS assets (
    id                    TEXT PRIMARY KEY,
    series_id             TEXT NOT NULL REFERENCES series(id),
    kind                  TEXT NOT NULL
                          CHECK (kind IN ('character', 'object', 'background')),
    display_name          TEXT NOT NULL,
    importance            TEXT NOT NULL DEFAULT 'normal'
                          CHECK (importance IN ('normal', 'story', 'required')),
    active_revision       INTEGER,
    source_occurrences    TEXT NOT NULL DEFAULT '[]',  -- JSON list of panel ids
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    UNIQUE (series_id, kind, display_name)
);

CREATE TABLE IF NOT EXISTS asset_revisions (
    id                    TEXT PRIMARY KEY,
    asset_id              TEXT NOT NULL REFERENCES assets(id),
    revision              INTEGER NOT NULL CHECK (revision > 0),
    payload               TEXT NOT NULL,  -- versioned JSON, validated on read
    origin                TEXT NOT NULL DEFAULT 'generated'
                          CHECK (origin IN ('uploaded', 'generated', 'registry-v1')),
    artifact_ids          TEXT NOT NULL DEFAULT '[]',  -- JSON list
    parent_revision       INTEGER,
    created_at            TEXT NOT NULL,
    UNIQUE (asset_id, revision)
);

CREATE TABLE IF NOT EXISTS asset_approvals (
    id              TEXT PRIMARY KEY,
    asset_id        TEXT NOT NULL REFERENCES assets(id),
    revision        INTEGER NOT NULL CHECK (revision > 0),
    state           TEXT NOT NULL
                    CHECK (state IN ('draft', 'awaiting_review', 'approved',
                                     'rejected', 'superseded')),
    decision_note   TEXT NOT NULL DEFAULT '',
    approved_by     TEXT NOT NULL DEFAULT '',
    approved_at     TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE (asset_id, revision)
);

CREATE TABLE IF NOT EXISTS asset_occurrence_bindings (
    id              TEXT PRIMARY KEY,
    series_id       TEXT NOT NULL REFERENCES series(id),
    subject_kind    TEXT NOT NULL CHECK (subject_kind IN ('panel', 'shot')),
    panel_id        TEXT,               -- set when subject_kind='panel'
    shot_id         TEXT,               -- set when subject_kind='shot'
    asset_id        TEXT REFERENCES assets(id),  -- NULL for unknown/ambiguous
    asset_revision  INTEGER NOT NULL DEFAULT 0,
    state           TEXT NOT NULL DEFAULT 'bound'
                    CHECK (state IN ('bound', 'unknown', 'ambiguous')),
    binding_revision INTEGER NOT NULL CHECK (binding_revision > 0),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    CHECK ((subject_kind = 'panel' AND panel_id IS NOT NULL)
           OR (subject_kind = 'shot' AND shot_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS story_beats (
    id              TEXT PRIMARY KEY,
    series_id       TEXT NOT NULL REFERENCES series(id),
    chapter_id      TEXT NOT NULL REFERENCES chapters(id),
    plan_revision   INTEGER NOT NULL CHECK (plan_revision > 0),
    beat_index      INTEGER NOT NULL CHECK (beat_index > 0),
    source_panel_ids TEXT NOT NULL,      -- JSON list, ordered
    supporting_text TEXT NOT NULL DEFAULT '',
    participants    TEXT NOT NULL DEFAULT '[]',   -- JSON list of asset ids
    dialogue_line_ids TEXT NOT NULL DEFAULT '[]', -- JSON list
    allowed_motion  TEXT NOT NULL DEFAULT '[]',   -- JSON list
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (chapter_id, plan_revision, beat_index)
);

CREATE TABLE IF NOT EXISTS shot_plan_revisions (
    id              TEXT PRIMARY KEY,
    series_id       TEXT NOT NULL REFERENCES series(id),
    chapter_id      TEXT NOT NULL REFERENCES chapters(id),
    shot_id         TEXT NOT NULL,
    revision        INTEGER NOT NULL CHECK (revision > 0),
    beat_coverage   TEXT NOT NULL,       -- JSON ordered list of story_beat ids
    source_panel_ids TEXT NOT NULL,      -- JSON list
    purpose         TEXT NOT NULL DEFAULT '',
    start_state     TEXT NOT NULL DEFAULT '',
    end_state       TEXT NOT NULL DEFAULT '',
    action_beats    TEXT NOT NULL DEFAULT '[]',   -- JSON list
    camera          TEXT NOT NULL DEFAULT '{}',   -- JSON: size, angle, action
    duration_policy TEXT NOT NULL DEFAULT '{}',   -- JSON
    route           TEXT NOT NULL DEFAULT 'direct_r2v'
                    CHECK (route IN ('direct_r2v', 'composition_assisted')),
    route_reason    TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (shot_id, revision)
);

CREATE TABLE IF NOT EXISTS shot_ratio_specs (
    id              TEXT PRIMARY KEY,
    shot_revision_id TEXT NOT NULL REFERENCES shot_plan_revisions(id),
    ratio           TEXT NOT NULL,
    target_width    INTEGER NOT NULL CHECK (target_width > 0),
    target_height   INTEGER NOT NULL CHECK (target_height > 0),
    placement       TEXT NOT NULL DEFAULT '{}',   -- JSON: scale, position, safe areas
    framing_note    TEXT NOT NULL DEFAULT '',
    reference_manifest TEXT NOT NULL DEFAULT '[]', -- JSON labelled slot manifest
    generation_profile TEXT,
    dependency_fingerprint TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (shot_revision_id, ratio)
);

CREATE TABLE IF NOT EXISTS shot_candidates (
    id              TEXT PRIMARY KEY,
    series_id       TEXT NOT NULL REFERENCES series(id),
    shot_revision_id TEXT NOT NULL REFERENCES shot_plan_revisions(id),
    ratio           TEXT NOT NULL,
    candidate_index INTEGER NOT NULL CHECK (candidate_index > 0),
    artifact_id     TEXT NOT NULL REFERENCES artifacts(id),
    input_fingerprint TEXT NOT NULL,
    acceptance      TEXT NOT NULL DEFAULT 'pending'
                    CHECK (acceptance IN ('pending', 'accepted', 'rejected', 'superseded')),
    rejection_note  TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (shot_revision_id, ratio, candidate_index)
);

CREATE TABLE IF NOT EXISTS run_plans (
    id              TEXT PRIMARY KEY,
    series_id       TEXT NOT NULL REFERENCES series(id),
    chapter_id      TEXT NOT NULL REFERENCES chapters(id),
    revision        INTEGER NOT NULL CHECK (revision > 0),
    frozen_snapshot TEXT NOT NULL,       -- JSON: shot revisions, ratios, pins
    required_ratios TEXT NOT NULL,       -- JSON list
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (chapter_id, revision)
);
"""



# ── v3 migration (R1.1): allow NULL asset_id for unknown/ambiguous bindings ──
_MIGRATION_V3 = """
CREATE TABLE IF NOT EXISTS asset_occurrence_bindings_new (
    id              TEXT PRIMARY KEY,
    series_id       TEXT NOT NULL REFERENCES series(id),
    subject_kind    TEXT NOT NULL CHECK (subject_kind IN ('panel', 'shot')),
    panel_id        TEXT,
    shot_id         TEXT,
    asset_id        TEXT REFERENCES assets(id),
    asset_revision  INTEGER NOT NULL DEFAULT 0,
    state           TEXT NOT NULL DEFAULT 'bound'
                    CHECK (state IN ('bound', 'unknown', 'ambiguous')),
    binding_revision INTEGER NOT NULL CHECK (binding_revision > 0),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    CHECK ((subject_kind = 'panel' AND panel_id IS NOT NULL)
           OR (subject_kind = 'shot' AND shot_id IS NOT NULL))
);
INSERT INTO asset_occurrence_bindings_new
    SELECT id, series_id, subject_kind, panel_id, shot_id, asset_id,
           asset_revision, state, binding_revision, created_at, updated_at
    FROM asset_occurrence_bindings;
DROP TABLE asset_occurrence_bindings;
ALTER TABLE asset_occurrence_bindings_new RENAME TO asset_occurrence_bindings;
"""


class Database:
    """Connection policy + schema/migrations for one data root."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "state" / DB_NAME
        self._local = threading.local()
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ── connection policy ────────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path, timeout=10.0, check_same_thread=True  # one connection per thread
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def connection(self) -> sqlite3.Connection:
        """Thread-local connection; created lazily and reused."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        """One SHORT explicit transaction. Commit on success; roll back on error.

        Never do slow work inside: no model calls, uploads, downloads, hashing
        of large files, media decode, process waits or user questions (§5.1).
        """
        conn = self.connection()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise

    # ── schema lifecycle ─────────────────────────────────────────────────────
    def initialize(self) -> None:
        """Create or migrate the schema. Refuses unknown nonempty legacy data (D01)."""
        state_dir = self.data_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        fresh = not self.path.exists()
        conn = self.connection()
        try:
            conn.execute("BEGIN")
            row = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            tables = {r[0] for r in row}
            version = 0
            if "schema_migrations" in tables:
                version = int(
                    conn.execute(
                        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                    ).fetchone()[0]
                )
            elif tables - {"sqlite_sequence"}:
                # Nonempty unrecognized store: stop explicitly, preserve it (D01).
                conn.rollback()
                raise RuntimeError(
                    f"unrecognized nonempty store at {self.path} — "
                    "refusing to initialize over unknown data; move it aside or delete it manually"
                )
            if version >= SCHEMA_VERSION:
                conn.rollback()
                return
            if version < 1:
                conn.executescript(_SCHEMA_V1)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at)"
                    " VALUES (?, datetime('now'))",
                    (1,),
                )
                version = 1
            if version < 2:
                conn.executescript(_MIGRATION_V2)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at)"
                    " VALUES (?, datetime('now'))",
                    (2,),
                )
                version = 2
            if version < 3:
                # v2 stores created asset_id NOT NULL; rebuild nullable (R1.1).
                conn.executescript(_MIGRATION_V3)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at)"
                    " VALUES (?, datetime('now'))",
                    (3,),
                )
                version = 3
            conn.commit()
        except BaseException:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise
        if fresh:
            log.info("created schema v%d at %s", SCHEMA_VERSION, self.path)

    def schema_version(self) -> int:
        try:
            row = self.connection().execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row[0]) if row else 0

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ── kv helpers (op tokens, lightweight flags — NOT a second domain store) ──
    def kv_get(self, key: str) -> str | None:
        row = self.connection().execute(
            "SELECT value FROM kv_meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def kv_set(self, key: str, value: str) -> None:
        with self.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO kv_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


__all__ = ["SCHEMA_VERSION", "Database"]
