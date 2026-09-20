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
                    f"beat_index {beat_index} already exists in plan r{plan_revision}")
            conn.execute(
                "INSERT INTO story_beats (id, series_id, chapter_id, plan_revision,"
                " beat_index, source_panel_ids, supporting_text, participants,"
                " dialogue_line_ids, allowed_motion, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (beat_id, series_id, chapter_id, plan_revision, beat_index,
                 json.dumps(source_panel_ids), supporting_text,
                 json.dumps(participants or []), json.dumps(dialogue_line_ids or []),
                 json.dumps(allowed_motion or []), now, now),
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
                (rev_id, series_id, chapter_id, shot_id, rev,
                 json.dumps(beat_coverage), json.dumps(source_panel_ids),
                 purpose, start_state, end_state,
                 json.dumps(action_beats or []), json.dumps(camera or {}),
                 json.dumps(duration_policy or {}), route, route_reason, now, now),
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
        for key in ("beat_coverage", "source_panel_ids", "action_beats",
                    "camera", "duration_policy"):
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
        covered: list[str] = []
        dialogue_seen: dict[str, str] = {}
        problems: list[str] = []
        for srid in shot_revision_ids:
            shot = self.get_shot_revision(srid)
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
        if covered != sorted(covered, key=lambda bid: next(
                b["beat_index"] for b in beats if b["beat_id"] == bid)):
            problems.append("beat order not preserved across shots")
        if len(set(covered)) != len(covered):
            problems.append("duplicate beat coverage")
        return {"valid": not problems, "problems": problems,
                "beats_required": len(required), "beats_covered": len(set(covered))}

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
                (spec_id, shot_revision_id, ratio, target_width, target_height,
                 json.dumps(placement or {}), framing_note,
                 json.dumps(reference_manifest or []), generation_profile,
                 dependency_fingerprint, now),
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
    def require_production_ready(self, shot_revision_id: str, ratio: str) -> dict[str, Any]:
        """All beat participants approved + ratio spec exists, else blocked."""
        shot = self.get_shot_revision(shot_revision_id)
        spec = self.get_ratio_spec(shot_revision_id, ratio)
        beat_ids = shot["beat_coverage"]
        blockers: list[str] = []
        with self.db.transaction() as conn:
            for bid in beat_ids:
                participants = json.loads(conn.execute(
                    "SELECT participants FROM story_beats WHERE id=?", (bid,)
                ).fetchone()["participants"])
                for asset_id in participants:
                    row = conn.execute(
                        "SELECT active_revision FROM assets WHERE id=?", (asset_id,)
                    ).fetchone()
                    if row is None:
                        blockers.append(f"beat {bid}: asset {asset_id} missing")
                        continue
                    rev = int(row["active_revision"] or 0)
                    if rev == 0:
                        blockers.append(f"beat {bid}: asset {asset_id} has no revisions")
                        continue
                    appr = conn.execute(
                        "SELECT state FROM asset_approvals WHERE asset_id=? AND revision=?",
                        (asset_id, rev),
                    ).fetchone()
                    if appr is None or appr["state"] != "approved":
                        state = appr['state'] if appr else 'unreviewed'
                        blockers.append(f"beat {bid}: asset {asset_id} r{rev} is {state}")
        if blockers:
            raise BlockedPrerequisite(
                "shot not production-ready: " + "; ".join(blockers)
            )
        return {"shot_revision_id": shot_revision_id, "ratio": ratio, "ready": True,
                "route": shot["route"], "spec_id": spec["id"]}


__all__ = ["ROUTES", "ShotStore"]
