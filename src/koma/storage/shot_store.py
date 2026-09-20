"""R1 — shot store: story beats, shot plan revisions, ratio specs, candidates.

Shot subjects are FIRST-CLASS: several panels may form one shot; one panel may
form several shots. Coverage validation is the completion authority (§C):
every required beat covered, dialogue lines delivered exactly once per ratio.
"""

from __future__ import annotations

import json
from typing import Any

from ..util import new_id, utcnow_iso
from .asset_store import AssetStore
from .errors import BlockedPrerequisite, InvalidTransition, NotFound

ROUTES = ("direct_r2v", "composition_assisted")


class ShotStore:
    """Story beats + revisioned shot plans + per-ratio specs + candidates."""

    def __init__(self, db, asset_store: AssetStore | None = None):
        self.db = db
        self.assets = asset_store or AssetStore(db)

    # ── story beats ───────────────────────────────────────────────────────
    def add_beat(
        self,
        series_id: str,
        chapter_id: str,
        plan_revision: int,
        *,
        beat_index: int,
        source_panel_ids: list[str],
        supporting_text: str = "",
        participants: list[str] | None = None,
        dialogue_line_ids: list[str] | None = None,
        allowed_motion: list[str] | None = None,
    ) -> dict[str, Any]:
        beat_id = f"beat:{new_id()}"
        now = utcnow_iso()
        with self.db.transaction() as conn:
            dup = conn.execute(
                "SELECT id FROM story_beats WHERE chapter_id=? AND plan_revision=?"
                " AND beat_index=?",
                (chapter_id, plan_revision, beat_index),
            ).fetchone()
            if dup:
                raise InvalidTransition(
                    f"beat_index {beat_index} already exists in plan r{plan_revision}"
                )
            conn.execute(
                "INSERT INTO story_beats (id, series_id, chapter_id, plan_revision,"
                " beat_index, source_panel_ids, supporting_text, participants,"
                " dialogue_line_ids, allowed_motion, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    beat_id,
                    series_id,
                    chapter_id,
                    plan_revision,
                    beat_index,
                    json.dumps(source_panel_ids),
                    supporting_text,
                    json.dumps(participants or []),
                    json.dumps(dialogue_line_ids or []),
                    json.dumps(allowed_motion or []),
                    now,
                    now,
                ),
            )
        return {"beat_id": beat_id, "beat_index": beat_index}

    def beats_for_plan(self, chapter_id: str, plan_revision: int) -> list[dict[str, Any]]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM story_beats WHERE chapter_id=? AND plan_revision=?"
                " ORDER BY beat_index",
                (chapter_id, plan_revision),
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["beat_id"] = d["id"]
            for key in ("source_panel_ids", "participants", "dialogue_line_ids", "allowed_motion"):
                d[key] = json.loads(d[key])
            out.append(d)
        return out

    # ── shot plan revisions ───────────────────────────────────────────────
    def add_shot_revision(
        self,
        series_id: str,
        chapter_id: str,
        *,
        beat_coverage: list[str],
        source_panel_ids: list[str],
        purpose: str = "",
        start_state: str = "",
        end_state: str = "",
        action_beats: list[dict] | None = None,
        camera: dict | None = None,
        duration_policy: dict | None = None,
        route: str = "direct_r2v",
        route_reason: str = "",
        shot_id: str | None = None,
    ) -> dict[str, Any]:
        if route not in ROUTES:
            raise ValueError(f"route must be one of {ROUTES}")
        if not beat_coverage:
            raise ValueError("a shot must cover at least one story beat")
        now = utcnow_iso()
        with self.db.transaction() as conn:
            if shot_id is None:
                shot_id = f"shot:{new_id()}"
            row = conn.execute(
                "SELECT COALESCE(MAX(revision), 0) AS max_rev FROM shot_plan_revisions"
                " WHERE shot_id=?",
                (shot_id,),
            ).fetchone()
            rev = int(row["max_rev"]) + 1
            rev_id = f"sprev:{new_id()}"
            conn.execute(
                "INSERT INTO shot_plan_revisions (id, series_id, chapter_id, shot_id,"
                " revision, beat_coverage, source_panel_ids, purpose, start_state,"
                " end_state, action_beats, camera, duration_policy, route,"
                " route_reason, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rev_id,
                    series_id,
                    chapter_id,
                    shot_id,
                    rev,
                    json.dumps(beat_coverage),
                    json.dumps(source_panel_ids),
                    purpose,
                    start_state,
                    end_state,
                    json.dumps(action_beats or []),
                    json.dumps(camera or {}),
                    json.dumps(duration_policy or {}),
                    route,
                    route_reason,
                    now,
                    now,
                ),
            )
        return {"shot_revision_id": rev_id, "shot_id": shot_id, "revision": rev}

    def get_shot_revision(self, shot_revision_id: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM shot_plan_revisions WHERE id=?", (shot_revision_id,)
            ).fetchone()
        if row is None:
            raise NotFound(f"shot revision {shot_revision_id}")
        d = dict(row)
        for key in (
            "beat_coverage",
            "source_panel_ids",
            "action_beats",
            "camera",
            "duration_policy",
        ):
            d[key] = json.loads(d[key])
        return d

    # ── coverage validation (completion authority) ────────────────────────
    def validate_coverage(
        self, chapter_id: str, plan_revision: int, shot_revision_ids: list[str]
    ) -> dict[str, Any]:
        """Every required beat covered exactly in order; no skipped beats;
        dialogue lines delivered; grouped shots preserve beat order."""
        beats = self.beats_for_plan(chapter_id, plan_revision)
        required = {b["beat_id"] for b in beats}
        indices = {b["beat_id"]: b["beat_index"] for b in beats}
        lines = set()
        covered: list[str] = []
        dialogue_seen: dict[str, str] = {}
        problems: list[str] = []
        if not beats:
            problems.append("plan has no story beats")
        for beat in beats:
            for line in beat["dialogue_line_ids"]:
                if line in lines:
                    problems.append(f"dialogue line delivered more than once: {line}")
                lines.add(line)
        for srid in shot_revision_ids:
            shot = self.get_shot_revision(srid)
            if shot["chapter_id"] != chapter_id:
                problems.append(f"shot {srid} belongs to another chapter")
            for bid in shot["beat_coverage"]:
                if bid not in required:
                    problems.append(f"shot {srid} covers unknown beat {bid}")
                if bid in dialogue_seen:
                    problems.append(f"beat {bid} covered by multiple shots in order")
                dialogue_seen.setdefault(bid, srid)
                covered.append(bid)
        missing = required - set(covered)
        if missing:
            problems.append(f"uncovered beats: {sorted(missing)}")
        known = [bid for bid in covered if bid in indices]
        if known != sorted(known, key=indices.__getitem__):
            problems.append("beat order not preserved across shots")
        if len(set(covered)) != len(covered):
            problems.append("duplicate beat coverage")
        return {
            "valid": not problems,
            "problems": problems,
            "beats_required": len(required),
            "beats_covered": len(set(covered)),
        }

    # ── ratio specs ───────────────────────────────────────────────────────
    def add_ratio_spec(
        self,
        shot_revision_id: str,
        ratio: str,
        *,
        target_width: int,
        target_height: int,
        placement: dict | None = None,
        framing_note: str = "",
        reference_manifest: list[dict] | None = None,
        generation_profile: str | None = None,
        dependency_fingerprint: str = "",
    ) -> dict[str, Any]:
        spec_id = f"rspec:{new_id()}"
        now = utcnow_iso()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM shot_plan_revisions WHERE id=?", (shot_revision_id,)
            ).fetchone()
            if row is None:
                raise NotFound(f"shot revision {shot_revision_id}")
            dup = conn.execute(
                "SELECT id FROM shot_ratio_specs WHERE shot_revision_id=? AND ratio=?",
                (shot_revision_id, ratio),
            ).fetchone()
            if dup:
                raise InvalidTransition(f"ratio {ratio} already specified for {shot_revision_id}")
            conn.execute(
                "INSERT INTO shot_ratio_specs (id, shot_revision_id, ratio,"
                " target_width, target_height, placement, framing_note,"
                " reference_manifest, generation_profile, dependency_fingerprint,"
                " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    spec_id,
                    shot_revision_id,
                    ratio,
                    target_width,
                    target_height,
                    json.dumps(placement or {}),
                    framing_note,
                    json.dumps(reference_manifest or []),
                    generation_profile,
                    dependency_fingerprint,
                    now,
                ),
            )
        return {"spec_id": spec_id, "ratio": ratio}

    def get_ratio_spec(self, shot_revision_id: str, ratio: str) -> dict[str, Any]:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM shot_ratio_specs WHERE shot_revision_id=? AND ratio=?",
                (shot_revision_id, ratio),
            ).fetchone()
        if row is None:
            raise NotFound(f"ratio spec {shot_revision_id}/{ratio}")
        d = dict(row)
        d["placement"] = json.loads(d["placement"])
        d["reference_manifest"] = json.loads(d["reference_manifest"])
        return d

    # ── production gate ───────────────────────────────────────────────────
    def require_production_ready(
        self, shot_revision_id: str, ratio: str, *, conn=None
    ) -> dict[str, Any]:
        """Validate exact frozen reference bindings, including revocation.

        Accept a caller transaction so promotion can fence approvals atomically.
        A newer unapproved proposal never silently rebinds an old approved shot.
        """
        if conn is None:
            with self.db.transaction() as transaction:
                return self.require_production_ready(shot_revision_id, ratio, conn=transaction)
        shot = conn.execute(
            "SELECT * FROM shot_plan_revisions WHERE id=?", (shot_revision_id,)
        ).fetchone()
        spec = conn.execute(
            "SELECT * FROM shot_ratio_specs WHERE shot_revision_id=? AND ratio=?",
            (shot_revision_id, ratio),
        ).fetchone()
        if shot is None or spec is None:
            raise NotFound(f"shot/ratio {shot_revision_id}/{ratio}")
        blockers = []
        participants = set()
        for bid in json.loads(shot["beat_coverage"]):
            beat = conn.execute("SELECT * FROM story_beats WHERE id=?", (bid,)).fetchone()
            if (
                beat is None
                or beat["chapter_id"] != shot["chapter_id"]
                or beat["series_id"] != shot["series_id"]
            ):
                blockers.append(f"missing or foreign beat {bid}")
            else:
                participants.update(json.loads(beat["participants"]))
        panel_ids = json.loads(shot["source_panel_ids"])
        occurrences = conn.execute(
            "SELECT * FROM asset_occurrence_bindings WHERE series_id=?", (shot["series_id"],)
        ).fetchall()
        latest = {}
        for occurrence in occurrences:
            key = (
                occurrence["subject_kind"],
                occurrence["panel_id"] or occurrence["shot_id"],
                occurrence["asset_id"] or occurrence["id"],
            )
            if (
                key not in latest
                or occurrence["binding_revision"] > latest[key]["binding_revision"]
            ):
                latest[key] = occurrence
        required_bindings = {}
        for occurrence in latest.values():
            if occurrence["shot_id"] == shot["shot_id"] or occurrence["panel_id"] in panel_ids:
                if occurrence["state"] != "bound":
                    blockers.append("unresolved asset occurrence")
                elif occurrence["asset_id"]:
                    participants.add(occurrence["asset_id"])
                    required_bindings.setdefault(occurrence["asset_id"], set()).add(
                        occurrence["asset_revision"]
                    )
        refs = json.loads(spec["reference_manifest"])
        bound = {}
        for ref in refs:
            if ref.get("role") == "story_composition":
                continue
            asset_id, rev = ref.get("entity"), ref.get("asset_revision")
            asset = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
            approval = conn.execute(
                "SELECT state FROM asset_approvals WHERE asset_id=? AND revision=?", (asset_id, rev)
            ).fetchone()
            revision = conn.execute(
                "SELECT artifact_ids FROM asset_revisions WHERE asset_id=? AND revision=?",
                (asset_id, rev),
            ).fetchone()
            artifact = conn.execute(
                "SELECT * FROM artifacts WHERE id=?", (ref.get("artifact_id"),)
            ).fetchone()
            if (
                asset is None
                or asset["series_id"] != shot["series_id"]
                or approval is None
                or approval["state"] != "approved"
                or revision is None
                or ref.get("artifact_id") not in json.loads(revision["artifact_ids"])
                or artifact is None
                or artifact["series_id"] != shot["series_id"]
                or artifact["sha256"] != ref.get("sha256")
            ):
                blockers.append(f"invalid/unapproved exact reference {asset_id} r{rev}")
            if asset_id in bound and bound[asset_id] != rev:
                blockers.append(f"conflicting variants for {asset_id}")
            bound[asset_id] = rev
        for asset_id in participants - set(bound):
            blockers.append(f"asset {asset_id} lacks an exact approved reference binding")
        for asset_id, revisions in required_bindings.items():
            if revisions != {bound.get(asset_id)}:
                blockers.append(f"reference differs from current occurrence binding for {asset_id}")
        if blockers:
            raise BlockedPrerequisite("shot not production-ready: " + "; ".join(blockers))
        return {
            "shot_revision_id": shot_revision_id,
            "ratio": ratio,
            "ready": True,
            "route": shot["route"],
            "spec_id": spec["id"],
        }


__all__ = ["ROUTES", "ShotStore"]
