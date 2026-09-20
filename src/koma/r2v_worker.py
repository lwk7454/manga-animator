"""R5 — supervised R2V worker: single execution owner over §9 storage.

Drives one shot's ratio generation through the attempt lifecycle:

    claim → record intent → begin submission → remote submit →
    acceptance/uncertain → poll → download → validate → promote

Every phase is durably checkpointed in the attempts table BEFORE side
effects, so any crash (worker, API, browser, server) resumes by state:
- prepared: submit only if no submission boundary has been crossed
- submitting: reconcile; the server may already have accepted the request
- submission_uncertain: reconcile (§D06) — find-or-hold, never blind resubmit
- running: poll the recorded prompt id; nothing is re-sent
- downloading/validating: re-download/validate then promote
- interrupted/stale terminal outcomes are honest — no silent retry

Ownership: the worker holds the koma execution lock while executing only
(PLAN §R5). The server/API process stays lock-free (read-only + approvals).
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Any

from .log import get_logger
from .models.work import AttemptState
from .scheduler.ownership import OwnerBusy, OwnerLock
from .storage.domain_store import KomaStore
from .storage.errors import BlockedPrerequisite, InvalidTransition, NotFound, RevisionConflict

log = get_logger("r2v.worker")


class WorkerError(RuntimeError):
    """Actionable worker failure; always recorded on the attempt."""


@dataclass
class ShotRatioJob:
    """One work item: a shot ratio candidate to generate."""

    work_id: str
    scope_key: str
    stage: str
    ratio: str
    input_fingerprint: str
    desired_spec: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionOutcome:
    attempt_id: str
    status: str  # succeeded | failed | held | interrupted
    detail: str = ""
    prompt_id: str | None = None
    artifact_ids: list[str] = field(default_factory=list)


class R2VWorker:
    """Single supervised worker: claim → execute → promote one job at a time.

    Composable transport: the ``submitter``/``poller``/``fetcher`` callables
    are injected (real ComfyUI transport vs test doubles) so orchestration
    logic is testable without the GPU and the qualified probe remains the
    reference implementation of the transport contract.
    """

    def __init__(
        self,
        store: KomaStore,
        lock: OwnerLock,
        *,
        owner_label: str = "r2v-worker",
        submitter=None,  # (graph, upload_plan) -> prompt_id
        poller=None,  # (prompt_id) -> str status
        fetcher=None,  # (prompt_id, target_dir) -> list[dict] artifacts
        validator=None,  # (artifacts, spec) -> list[dict] validated
        server_identity: str = "comfyui",
        snapshotter=None,
    ):
        self.store = store
        self.lock = lock
        self.owner_epoch = f"r2v-worker-{time.time_ns()}"
        self._submitter = submitter
        self._poller = poller
        self._fetcher = fetcher
        self._validator = validator
        self.server_identity = server_identity
        self._snapshotter = snapshotter

    # ── job assembly ──────────────────────────────────────────────────────
    def assemble_jobs(self, chapter_id: str, ratios: list[str]) -> list[ShotRatioJob]:
        """Spec jobs for every pending shot ratio in the chapter (§E)."""
        jobs: list[ShotRatioJob] = []
        with self.store.db.transaction() as conn:
            rows = conn.execute(
                "SELECT w.id, w.scope_key, w.stage, w.ratio, w.mode,"
                " w.input_fingerprint, w.desired_spec, w.state FROM work_items w"
                " WHERE w.chapter_id = ? AND w.stage = 'motion' AND w.mode ="
                " 'production' AND w.ratio IN"
                " (" + ",".join("?" * len(ratios)) + ")"
                " AND w.state IN ('ready','blocked','active')"
                " ORDER BY w.priority, w.id",
                (chapter_id, *ratios),
            ).fetchall()
        for r in rows:
            jobs.append(
                ShotRatioJob(
                    work_id=r["id"],
                    scope_key=r["scope_key"],
                    stage=r["stage"],
                    ratio=r["ratio"],
                    input_fingerprint=r["input_fingerprint"],
                    desired_spec=json.loads(r["desired_spec"]),
                )
            )
        return jobs

    # ── single-attempt execution (the §9.2 steps) ─────────────────────────
    def execute(
        self,
        job: ShotRatioJob,
        *,
        owner_epoch: str | None = None,
        owner_description: str = "r2v execution",
    ) -> ExecutionOutcome:
        attempt_id = ""
        try:
            owner = self.lock.acquire(owner_description, self.store.db.data_dir)
        except OwnerBusy as exc:
            return ExecutionOutcome("", "owner_busy", str(exc))
        epoch = owner_epoch or owner.epoch
        try:
            with self.store.db.transaction(immediate=True) as conn:
                work = conn.execute(
                    "SELECT * FROM work_items WHERE id=?", (job.work_id,)
                ).fetchone()
                if work is None:
                    raise NotFound(job.work_id)
                if (
                    work["input_fingerprint"] != job.input_fingerprint
                    or json.loads(work["desired_spec"]) != job.desired_spec
                ):
                    raise RevisionConflict("job specification changed; reload work")
                if work["state"] == "succeeded":
                    from .storage.shot_store import ShotStore

                    ShotStore(self.store.db).require_production_ready(
                        job.desired_spec["shot_revision_id"], job.ratio, conn=conn
                    )
                    return ExecutionOutcome(
                        "", "succeeded", "generation completed; review/assembly are separate"
                    )
                row = conn.execute(
                    "SELECT * FROM attempts WHERE work_id=? ORDER BY attempt_number DESC LIMIT 1",
                    (job.work_id,),
                ).fetchone()
                if row and row["state"] in {
                    "prepared",
                    "submitting",
                    "submission_uncertain",
                    "running",
                    "downloading",
                    "validating",
                }:
                    attempt_id = row["id"]
                    # Adopt only this attempt, while holding the host execution lock.
                    conn.execute(
                        "UPDATE attempts SET owner_epoch=? WHERE id=?", (epoch, attempt_id)
                    )
                elif (
                    row
                    and row["state"] == "interrupted"
                    and row["remote_prompt_id"]
                    and row["server_identity"] == self.server_identity
                    and work["state"] == "blocked"
                    and (work["blocked_reason"] or "").startswith("interrupted:")
                ):
                    # Recover old deadline records without allocating another attempt.
                    attempt_id = row["id"]
                    conn.execute(
                        "UPDATE attempts SET owner_epoch=?, state='running', completed_at="
                        "NULL WHERE id=?",
                        (epoch, attempt_id),
                    )
                    conn.execute(
                        "UPDATE work_items SET state='active', blocked_reason='' WHERE id=?",
                        (job.work_id,),
                    )
                elif row:
                    raise BlockedPrerequisite(
                        "terminal attempt requires explicit retry/recovery review"
                    )
            if not all(
                callable(fn)
                for fn in (self._submitter, self._poller, self._fetcher, self._validator)
            ):
                raise BlockedPrerequisite(
                    "submit/poll/download/validation transport is not configured"
                )
            wait_seconds = float(job.desired_spec.get("wait_seconds", 1800))
            poll_seconds = float(job.desired_spec.get("poll_seconds", 5))
            if (
                not math.isfinite(wait_seconds)
                or wait_seconds < 0
                or not math.isfinite(poll_seconds)
                or poll_seconds <= 0
            ):
                raise WorkerError("wait/poll durations must be finite; wait >= 0 and poll > 0")
            self._check_production(job)
            if not attempt_id:
                with self.store.db.transaction() as conn:
                    unfinished = conn.execute(
                        "SELECT id FROM attempts WHERE state IN ('prepared','submitting','"
                        "submission_uncertain','running','downloading','validating') LIMIT"
                        " 1"
                    ).fetchone()
                if unfinished:
                    raise BlockedPrerequisite(
                        "resume/reconcile outstanding attempt before new generation"
                    )
                from .motion_review import MotionReviewService

                MotionReviewService(self.store.db).next_retry_spec(
                    job.desired_spec["shot_revision_id"], job.ratio
                )
                attempt_id = self.store.claim_attempt(job.work_id, epoch)["attempt_id"]
            return self._drive(attempt_id, job, epoch)
        except (
            WorkerError,
            InvalidTransition,
            BlockedPrerequisite,
            RevisionConflict,
            NotFound,
        ) as exc:
            return ExecutionOutcome(attempt_id, "held", f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            # Leave durable phase and accepted prompt intact. Resume never submits
            # an accepted attempt again, including after poll/download failures.
            return ExecutionOutcome(attempt_id, "held", f"transport/validation: {exc}")
        finally:
            self.lock.release(owner)

    def _check_production(self, job):
        from .storage.shot_store import ShotStore

        shot_revision = job.desired_spec.get("shot_revision_id")
        if not shot_revision:
            raise BlockedPrerequisite("production work requires shot_revision_id")
        ready = ShotStore(self.store.db).require_production_ready(shot_revision, job.ratio)
        if ready["route"] != "direct_r2v":
            raise BlockedPrerequisite("composition-assisted execution is not implemented")

    def _drive(self, attempt_id: str, job: ShotRatioJob, epoch: str) -> ExecutionOutcome:
        spec = job.desired_spec
        graph = spec.get("compiled_graph")
        if not graph:
            raise WorkerError("desired_spec lacks compiled_graph (compile before execute)")
        upload_plan = spec.get("upload_plan") or []
        output_dir = self.store.db.data_dir / "attempts" / attempt_id
        output_dir.mkdir(parents=True, exist_ok=True)

        row = self._attempt(attempt_id)
        if row["compiled_workflow_hash"] and row["compiled_workflow_hash"] != self._graph_hash_of(
            graph
        ):
            raise WorkerError("frozen attempt graph differs from current job; hold for review")
        if row["state"] == "prepared":
            request_identity = f"r2v:{attempt_id}"
            self.store.record_attempt_intent(
                attempt_id,
                epoch,
                request_identity=request_identity,
                seed=spec.get("seed"),
                upload_plan=[u["upload_name"] for u in upload_plan],
                output_prefix=spec.get("output_prefix", "koma"),
                compiled_workflow_hash=self._graph_hash_of(graph),
            )
            # Store the server before the uncertain network boundary.
            with self.store.db.transaction(immediate=True) as conn:
                conn.execute(
                    "UPDATE attempts SET server_identity=? WHERE id=? AND owner_epoch=?",
                    (self.server_identity, attempt_id, epoch),
                )
            self.store.begin_submission(attempt_id, epoch)
            try:
                prompt_id = self._submitter(
                    graph,
                    upload_plan,
                    {"request_identity": request_identity, "server_identity": self.server_identity},
                )
                if not isinstance(prompt_id, str) or not prompt_id.strip():
                    raise WorkerError("invalid remote prompt ID")
            except Exception as exc:
                self.store.mark_submission_uncertain(attempt_id, epoch, str(exc)[:2000])
                return ExecutionOutcome(attempt_id, "held", "submission_uncertain (D06); reconcile")
            self.store.record_remote_acceptance(attempt_id, epoch, prompt_id, self.server_identity)
        elif row["state"] in {"submitting", "submission_uncertain"}:
            if row["state"] == "submitting":
                self.store.mark_submission_uncertain(
                    attempt_id, epoch, "recovered submission boundary"
                )
            result = self._reconcile_owned(attempt_id, epoch)
            if result.status != "accepted":
                return result
        row = self._attempt(attempt_id)
        prompt_id = row["remote_prompt_id"]
        if not prompt_id or row["server_identity"] != self.server_identity:
            raise WorkerError("accepted prompt/server identity missing or changed")

        # 3. poll to completion
        deadline = time.monotonic() + float(spec.get("wait_seconds", 1800))
        poll_seconds = float(spec.get("poll_seconds", 5))
        while row["state"] == "running":
            status = self._poller(prompt_id)
            if status == "generated":
                break
            if status == "failed":
                self.store.record_attempt_outcome(
                    attempt_id,
                    epoch,
                    AttemptState.FAILED,
                    error_kind="remote_failed",
                    error_detail=f"prompt {prompt_id}",
                )
                return ExecutionOutcome(
                    attempt_id=attempt_id,
                    status="failed",
                    detail="remote generation failed",
                    prompt_id=prompt_id,
                )
            if time.monotonic() >= deadline:
                # RUNNING remotely; resume polls the same prompt id (§9.3)
                return ExecutionOutcome(
                    attempt_id,
                    "interrupted",
                    "accepted work continues remotely; resume to poll",
                    prompt_id,
                )
            time.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))
        if row["state"] == "running":
            self.store.record_attempt_outcome(attempt_id, epoch, AttemptState.DOWNLOADING)

        # 4. download + validate + promote
        try:
            artifacts = self._fetcher(prompt_id, output_dir)
            validated = self._validator(artifacts, spec)
            if not validated:
                raise WorkerError("no validated output artifacts")
            if self._attempt(attempt_id)["state"] == "downloading":
                self.store.record_attempt_outcome(attempt_id, epoch, AttemptState.VALIDATING)
            self._check_production(job)
            promoted = self.store.promote_artifacts(
                attempt_id, epoch, job.input_fingerprint, validated
            )
        except (WorkerError, InvalidTransition, RevisionConflict) as exc:
            return ExecutionOutcome(attempt_id, "held", str(exc), prompt_id)
        return ExecutionOutcome(
            attempt_id=attempt_id,
            status="succeeded",
            prompt_id=prompt_id,
            artifact_ids=promoted["artifact_ids"],
        )

    # ── reconciliation (§D06) ─────────────────────────────────────────────
    def reconcile_uncertain(self, attempt_id: str, epoch: str) -> ExecutionOutcome:
        """Find-or-hold for a submission_uncertain attempt: look for a unique
        matching prompt id in queue/history; record acceptance or stay held.
        NEVER resubmits (the request may have landed).

        Fencing: the reconciler adopts the attempt first (epoch transfer) —
        reconciliation is precisely the post-crash path; the old owner's
        callbacks can no longer write (§9.1a)."""
        try:
            owner = self.lock.acquire("r2v reconciliation", self.store.db.data_dir)
        except OwnerBusy as exc:
            return ExecutionOutcome(attempt_id, "owner_busy", str(exc))
        try:
            return self._reconcile_owned(attempt_id, owner.epoch)
        finally:
            self.lock.release(owner)

    def _reconcile_owned(self, attempt_id, epoch):
        row = self._attempt(attempt_id)
        if row["state"] != "submission_uncertain":
            raise InvalidTransition(f"attempt in state {row['state']}")
        with self.store.db.transaction(immediate=True) as conn:
            conn.execute("UPDATE attempts SET owner_epoch=? WHERE id=?", (epoch, attempt_id))
        found = self._reconcile_find(row)
        if found:
            self.store.record_remote_acceptance(
                attempt_id, epoch, found, server_identity=self.server_identity
            )
            return ExecutionOutcome(attempt_id=attempt_id, status="accepted", prompt_id=found)
        return ExecutionOutcome(
            attempt_id=attempt_id,
            status="held",
            detail="no unique matching request on the server; keep for review",
        )

    def _attempt(self, attempt_id: str) -> dict:
        with self.store.db.transaction() as conn:
            row = conn.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
        if row is None:
            raise NotFound(f"attempt {attempt_id!r}")
        return dict(row)

    @staticmethod
    def _graph_hash_of(graph: Any) -> str:
        import hashlib

        return hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()

    def _reconcile_find(self, row: dict) -> str | None:
        matches: set[str] = set()
        intent_hash = row.get("compiled_workflow_hash") or ""
        if not row.get("request_identity") or row.get("server_identity") != self.server_identity:
            return None
        remote = self._reconcile_snapshot()
        for pid, entry in remote.items():
            # Normalized adapter record: graph + exact extra_data identity + server.
            if not isinstance(entry, dict):
                continue
            graph = entry.get("graph")
            if (
                entry.get("request_identity") == row["request_identity"]
                and entry.get("server_identity") == row["server_identity"]
                and graph is not None
                and self._graph_hash_of(graph) == intent_hash
            ):
                matches.add(pid)
        if len(matches) == 1:
            return matches.pop()
        return None  # 0 or ambiguous (>1): stay held either way

    def _reconcile_snapshot(self) -> dict:
        """Server prompt_id → history/queue entry. Transport hook."""
        if self._snapshotter is not None:
            return self._snapshotter()
        if getattr(self, "_reconcile_entries", None):
            return self._reconcile_entries
        return {}

    def set_reconcile_snapshot(self, entries: dict) -> None:
        """Test/adapter hook: inject the server's queue+history view."""
        self._reconcile_entries = entries


__all__ = ["ExecutionOutcome", "R2VWorker", "ShotRatioJob", "WorkerError"]
