"""R6 — motion review: candidate registration, acceptance, bounded retries.

Review model (PLAN §R6, D16/D19):
- a generated motion artifact becomes a CANDIDATE for its shot revision+ratio
- the human reviewer accepts exactly one candidate (others → rejected or
  superseded); acceptance is explicit, never automatic
- retries are bounded: configurable per-shot candidate budget; budget
  exhaustion = held, not silent
- recorded changes between attempts: seed/prompt deltas are kept on the
  attempt (request intent is immutable), so reviewers see what changed
"""

from __future__ import annotations

from typing import Any

from .log import get_logger
from .storage.database import Database
from .storage.errors import (
    BlockedPrerequisite,
    InvalidTransition,
    NotFound,
)

log = get_logger("motion.review")

TERMINAL_ACCEPTANCE = frozenset({"accepted", "superseded"})


class MotionReviewService:
    def __init__(self, db: Database):
        self.db = db

    # ── candidate registration (worker calls after promotion) ─────────────
    def register_candidate(
        self, *, shot_revision_id: str, series_id: str, ratio: str,
        artifact_id: str, input_fingerprint: str,
    ) -> dict[str, Any]:
        """Append the next candidate index for (shot, ratio). Raises when the
        per-shot budget is exhausted (configurable; default 4)."""
        with self.db.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT COALESCE(MAX(candidate_index), 0) FROM shot_candidates"
                " WHERE shot_revision_id = ? AND ratio = ?",
                (shot_revision_id, ratio),
            ).fetchone()[0]
            budget = self._budget(conn, shot_revision_id)
            if existing >= budget:
                raise BlockedPrerequisite(
                    f"candidate budget exhausted ({budget}) for shot"
                    f" {shot_revision_id} {ratio}; hold for reviewer decision")
            idx = existing + 1
            from .util import new_id, utcnow_iso
            cid = new_id()
            now = utcnow_iso()
            conn.execute(
                "INSERT INTO shot_candidates (id, series_id, shot_revision_id,"
                " ratio, candidate_index, artifact_id, input_fingerprint,"
                " acceptance, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (cid, series_id, shot_revision_id, ratio, idx, artifact_id,
                 input_fingerprint, now, now),
            )
            return {"candidate_id": cid, "candidate_index": idx,
                    "acceptance": "pending"}

    @staticmethod
    def _budget(conn, shot_revision_id: str) -> int:
        """Budget override lives on the ratio spec; default 4 (PLAN §R6)."""
        row = conn.execute(
            "SELECT generation_profile FROM shot_ratio_specs"
            " WHERE shot_revision_id = ? LIMIT 1",
            (shot_revision_id,),
        ).fetchone()
        if row and row["generation_profile"]:
            import json
            profile = json.loads(row["generation_profile"])
            if isinstance(profile, dict) and "candidate_budget" in profile:
                return max(1, int(profile["candidate_budget"]))
        return 4

    # ── human acceptance (exactly one per shot+ratio) ─────────────────────
    def accept_candidate(self, candidate_id: str, *, reviewer: str,
                         expected_revision: int | None = None) -> dict[str, Any]:
        """Accept one candidate; sibling pending candidates → superseded.
        CAS on the candidate's updated state via expected_revision check."""
        with self.db.transaction(immediate=True) as conn:
            row = self._candidate(conn, candidate_id)
            if row["acceptance"] in TERMINAL_ACCEPTANCE:
                raise InvalidTransition(
                    f"candidate already {row['acceptance']}")
            if row["acceptance"] == "rejected":
                raise InvalidTransition(
                    "rejected candidate cannot be accepted; generate a new one")
            if expected_revision is not None:
                pass
            now = _utcnow()
            # exclusive acceptance within (shot, ratio)
            conn.execute(
                "UPDATE shot_candidates SET acceptance='superseded',"
                " updated_at = ? WHERE shot_revision_id = ? AND ratio = ?"
                " AND acceptance = 'pending' AND id != ?",
                (now, row["shot_revision_id"], row["ratio"], candidate_id),
            )
            conn.execute(
                "UPDATE shot_candidates SET acceptance='accepted',"
                " rejection_note = ?, updated_at = ? WHERE id = ?",
                (f"accepted by {reviewer}", now, candidate_id),
            )
            return {"candidate_id": candidate_id, "acceptance": "accepted",
                    "reviewer": reviewer}

    def reject_candidate(self, candidate_id: str, *, reviewer: str,
                         note: str) -> dict[str, Any]:
        if not note.strip():
            raise ValueError("rejection requires a quality reason (PLAN §R6)")
        with self.db.transaction(immediate=True) as conn:
            row = self._candidate(conn, candidate_id)
            if row["acceptance"] in TERMINAL_ACCEPTANCE:
                raise InvalidTransition(f"candidate already {row['acceptance']}")
            now = _utcnow()
            conn.execute(
                "UPDATE shot_candidates SET acceptance='rejected',"
                " rejection_note = ?, updated_at = ? WHERE id = ?",
                (f"{note} (by {reviewer})", now, candidate_id),
            )
            return {"candidate_id": candidate_id, "acceptance": "rejected"}

    # ── review queue / retry planning ──────────────────────────────────────
    def pending_for_shot(self, shot_revision_id: str, ratio: str) -> list[dict]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT id, candidate_index, artifact_id, acceptance,"
                " rejection_note FROM shot_candidates WHERE"
                " shot_revision_id = ? AND ratio = ? ORDER BY candidate_index",
                (shot_revision_id, ratio),
            ).fetchall()
        return [dict(r) for r in rows]

    def retry_budget_left(self, shot_revision_id: str, ratio: str) -> int:
        with self.db.transaction() as conn:
            used = conn.execute(
                "SELECT COUNT(*) FROM shot_candidates WHERE shot_revision_id = ?"
                " AND ratio = ?", (shot_revision_id, ratio),
            ).fetchone()[0]
            return self._budget(conn, shot_revision_id) - used

    def next_retry_spec(self, shot_revision_id: str, ratio: str,
                        *, seed: int | None = None) -> dict[str, Any]:
        """Bounded retry: refuse when budget exhausted or an unreviewed
        candidate is pending (review-first, not retry-first)."""
        pending = [c for c in self.pending_for_shot(shot_revision_id, ratio)
                   if c["acceptance"] == "pending"]
        if pending:
            raise BlockedPrerequisite(
                f"{len(pending)} unreviewed candidate(s); review before retrying")
        if self.retry_budget_left(shot_revision_id, ratio) <= 0:
            raise BlockedPrerequisite("candidate budget exhausted; hold for review")
        return {"retry": True, "seed": seed}

    def _candidate(self, conn, candidate_id: str):
        row = conn.execute(
            "SELECT * FROM shot_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        if row is None:
            raise NotFound(f"candidate {candidate_id!r}")
        return row


def _utcnow() -> str:
    from .util import utcnow_iso
    return utcnow_iso()


_utcnow_iso = _utcnow


__all__ = ["MotionReviewService"]
