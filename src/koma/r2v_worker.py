"""R5 — supervised R2V worker: single execution owner over §9 storage.

Drives one shot's ratio generation through the attempt lifecycle:

    claim → record intent → begin submission → remote submit →
    acceptance/uncertain → poll → download → validate → promote

Every phase is durably checkpointed in the attempts table BEFORE side
effects, so any crash (worker, API, browser, server) resumes by state:
- prepared/submitting: resubmit with the SAME request identity
- submission_uncertain: reconcile (§D06) — find-or-hold, never blind resubmit
- running: poll the recorded prompt id; nothing is re-sent
- downloading/validating: re-download/validate then promote
- interrupted/stale terminal outcomes are honest — no silent retry

Ownership: the worker holds the koma execution lock while executing only
(PLAN §R5). The server/API process stays lock-free (read-only + approvals).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
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
    status: str          # succeeded | failed | held | interrupted
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
        submitter=None,       # (graph, upload_plan) -> prompt_id
        poller=None,          # (prompt_id) -> str status
        fetcher=None,         # (prompt_id, target_dir) -> list[dict] artifacts
        validator=None,       # (artifacts, spec) -> list[dict] validated
    ):
        self.store = store
        self.lock = lock
        self.owner_epoch = f"r2v-worker-{time.time_ns()}"
        self._submitter = submitter
        self._poller = poller
        self._fetcher = fetcher
        self._validator = validator

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
                " AND w.state IN ('ready','failed','blocked','active')"
                " ORDER BY w.priority, w.id",
                (chapter_id, *ratios),
            ).fetchall()
        for r in rows:
            jobs.append(ShotRatioJob(
                work_id=r["id"], scope_key=r["scope_key"], stage=r["stage"],
                ratio=r["ratio"], input_fingerprint=r["input_fingerprint"],
                desired_spec=json.loads(r["desired_spec"]),
            ))
        return jobs

    # ── single-attempt execution (the §9.2 steps) ─────────────────────────
    def execute(self, job: ShotRatioJob, *, owner_epoch: str | None = None,
                owner_description: str = "r2v execution") -> ExecutionOutcome:
        epoch = owner_epoch or self.owner_epoch
        lock, owner = self.lock, None
        try:
            owner = lock.acquire(owner_description, None)
        except OwnerBusy as exc:
            return ExecutionOutcome(attempt_id="", status="owner_busy",
                                    detail=str(exc))
        try:
            claim = self.store.claim_attempt(job.work_id, epoch)
            attempt_id = claim["attempt_id"]
            log.info("claimed %s for %s (attempt #%s)",
                     attempt_id, job.scope_key, claim["attempt_number"])
            try:
                return self._drive(attempt_id, job, epoch)
            except WorkerError as exc:
                self.store.record_attempt_outcome(
                    attempt_id, epoch, AttemptState.FAILED,
                    error_kind="worker", error_detail=str(exc)[:2000])
                return ExecutionOutcome(attempt_id=attempt_id, status="failed",
                                        detail=str(exc))
            except (InvalidTransition, BlockedPrerequisite, RevisionConflict, NotFound) as exc:
                # store-refused operations are honest outcomes, not crashes
                return ExecutionOutcome(attempt_id=attempt_id, status="held",
                                        detail=f"{type(exc).__name__}: {exc}")
            except KeyboardInterrupt:
                self.store.record_attempt_outcome(
                    attempt_id, epoch, AttemptState.INTERRUPTED,
                    error_detail="worker interrupted; resume this attempt")
                raise
        finally:
            if owner is not None:
                lock.release(owner)

    def _drive(self, attempt_id: str, job: ShotRatioJob, epoch: str) -> ExecutionOutcome:
        spec = job.desired_spec
        graph = spec.get("compiled_graph")
        if not graph:
            raise WorkerError("desired_spec lacks compiled_graph (compile before execute)")
        upload_plan = spec.get("upload_plan") or []
        output_dir = Path(spec.get("output_dir", "."))

        # 1. intent BEFORE side effects (§9.2.1)
        request_identity = f"r2v:{job.scope_key}:{job.ratio}:{job.input_fingerprint[:12]}"
        self.store.record_attempt_intent(
            attempt_id, epoch,
            request_identity=request_identity,
            seed=spec.get("seed"),
            upload_plan=[u["upload_name"] for u in upload_plan],
            output_prefix=spec.get("output_prefix", "koma"),
            compiled_workflow_hash=spec.get("compiled_workflow_hash"),
        )

        # 2. submission
        self.store.begin_submission(attempt_id, epoch)
        try:
            prompt_id = self._submitter(graph, upload_plan)
        except Exception as exc:  # transport-level: uncertain, never blind-retry
            self.store.mark_submission_uncertain(
                attempt_id, epoch, f"submit transport error: {exc}"[:2000])
            return ExecutionOutcome(attempt_id=attempt_id, status="held",
                                    detail="submission_uncertain (D06); reconcile")
        self.store.record_remote_acceptance(attempt_id, epoch, prompt_id,
                                            server_identity="comfyui")

        # 3. poll to completion
        deadline = time.monotonic() + float(spec.get("wait_seconds", 1800))
        poll_seconds = float(spec.get("poll_seconds", 5))
        while True:
            status = self._poller(prompt_id)
            if status == "generated":
                break
            if status == "failed":
                self.store.record_attempt_outcome(
                    attempt_id, epoch, AttemptState.FAILED,
                    error_kind="remote_failed", error_detail=f"prompt {prompt_id}")
                return ExecutionOutcome(attempt_id=attempt_id, status="failed",
                                        detail="remote generation failed",
                                        prompt_id=prompt_id)
            if time.monotonic() >= deadline:
                # RUNNING remotely; resume polls the same prompt id (§9.3)
                self.store.record_attempt_outcome(
                    attempt_id, epoch, AttemptState.INTERRUPTED,
                    error_detail=f"deadline: remote {prompt_id} continues; resume to poll")
                return ExecutionOutcome(attempt_id=attempt_id, status="interrupted",
                                        prompt_id=prompt_id,
                                        detail="accepted work continues remotely; resume to poll")
            time.sleep(poll_seconds)
        self.store.record_attempt_outcome(
            attempt_id, epoch, AttemptState.DOWNLOADING)

        # 4. download + validate + promote
        try:
            artifacts = self._fetcher(prompt_id, output_dir)
            validated = self._validator(artifacts, spec) if self._validator else artifacts
            self.store.record_attempt_outcome(
                attempt_id, epoch, AttemptState.VALIDATING)
            promoted = self.store.promote_artifacts(
                attempt_id, epoch, job.input_fingerprint, validated)
        except (WorkerError, InvalidTransition, RevisionConflict) as exc:
            self.store.record_attempt_outcome(
                attempt_id, epoch, AttemptState.FAILED,
                error_kind="validate", error_detail=str(exc)[:2000])
            return ExecutionOutcome(attempt_id=attempt_id, status="failed",
                                    detail=str(exc), prompt_id=prompt_id)
        return ExecutionOutcome(attempt_id=attempt_id, status="succeeded",
                                prompt_id=prompt_id,
                                artifact_ids=promoted["artifact_ids"])

    # ── reconciliation (§D06) ─────────────────────────────────────────────
    def reconcile_uncertain(self, attempt_id: str, epoch: str) -> ExecutionOutcome:
        """Find-or-hold for a submission_uncertain attempt: look for a unique
        matching prompt id in queue/history; record acceptance or stay held.
        NEVER resubmits (the request may have landed).

        Fencing: the reconciler adopts the attempt first (epoch transfer) —
        reconciliation is precisely the post-crash path; the old owner's
        callbacks can no longer write (§9.1a)."""
        row = self._attempt(attempt_id)
        if row["state"] != "submission_uncertain":
            raise InvalidTransition(f"attempt in state {row['state']}")
        if row["owner_epoch"] != epoch:
            adopted = self.store.adopt_nonterminal_attempts(row["owner_epoch"], epoch)
            if attempt_id not in adopted:
                raise RevisionConflict(
                    f"attempt {attempt_id} could not be adopted for reconciliation")
        found = self._reconcile_find(row)
        if found:
            self.store.record_remote_acceptance(
                attempt_id, epoch, found, server_identity="comfyui")
            return ExecutionOutcome(attempt_id=attempt_id, status="accepted",
                                    prompt_id=found)
        return ExecutionOutcome(
            attempt_id=attempt_id, status="held",
            detail="no unique matching request on the server; keep for review")

    def _attempt(self, attempt_id: str) -> dict:
        with self.store.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
        if row is None:
            raise NotFound(f"attempt {attempt_id!r}")
        return dict(row)

    @staticmethod
    def _graph_hash_of(graph: Any) -> str:
        import hashlib
        return hashlib.sha256(
            json.dumps(graph, sort_keys=True).encode()).hexdigest()

    def _reconcile_find(self, row: dict) -> str | None:
        matches: set[str] = set()
        intent_hash = row.get("compiled_workflow_hash") or ""
        remote = self._reconcile_snapshot()
        for pid, entry in remote.items():
            graph = (entry or {}).get("prompt", {}).get("prompt")
            if graph is None:
                continue
            if self._graph_hash_of(graph) == intent_hash:
                matches.add(pid)
        if len(matches) == 1:
            return matches.pop()
        return None  # 0 or ambiguous (>1): stay held either way

    def _reconcile_snapshot(self) -> dict:
        """Server prompt_id → history/queue entry. Transport hook."""
        if getattr(self, "_reconcile_entries", None):
            return self._reconcile_entries
        return {}

    def set_reconcile_snapshot(self, entries: dict) -> None:
        """Test/adapter hook: inject the server's queue+history view."""
        self._reconcile_entries = entries


__all__ = ["ExecutionOutcome", "R2VWorker", "ShotRatioJob", "WorkerError"]
