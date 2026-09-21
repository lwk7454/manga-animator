"""R5 — API run-control endpoints: enqueue shot ratio jobs + run status.

Wires the R2VWorker into the /api/v1 surface as async run actions:
- POST /api/v1/runs: enqueue compile+execute for a chapter's shot ratios
  (background thread owns the lock only while executing)
- GET /api/v1/runs: queue view (attempt states, work states, outcomes)
- POST /api/v1/attempts/{id}/reconcile: D06 find-or-hold
The API process itself never owns the GPU lock outside executing threads.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Literal

from fastapi import Request
from pydantic import BaseModel, Field

from ..r2v_worker import ExecutionOutcome, R2VWorker
from ..storage.errors import InvalidTransition, NotFound, RevisionConflict
from .api import _origin_allowed


class RunRegistry:
    """In-process view of worker runs + background execution threads.

    Durable state lives in the attempts table; this registry only tracks
    in-flight threads for the API's /runs listing and prevents double-start
    of the same work id within one process.
    """

    def __init__(self) -> None:
        self._inflight: dict[str, dict[str, Any]] = {}
        self._outcomes: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._execution = threading.Lock()

    def start(self, worker: R2VWorker, job: Any) -> dict[str, Any]:
        """Idempotent: an already-running job returns its live entry."""
        with self._lock:
            existing = self._inflight.get(job.work_id)
            if existing and existing["thread"].is_alive():
                return {
                    "work_id": job.work_id,
                    "status": "already_running",
                    "started_at": existing["started_at"],
                }
            record = {
                "thread": None,
                "started_at": time.time(),
                "work_id": job.work_id,
                "executing": False,
            }

            def _run():
                try:
                    with self._execution:
                        with self._lock:
                            record["executing"] = True
                        outcome = worker.execute(job)
                        self._record(job.work_id, outcome)
                except Exception as exc:
                    self._record(job.work_id, ExecutionOutcome("", "held", str(exc)[:2000]))
                finally:
                    worker.store.db.close()

            t = threading.Thread(target=_run, name=f"koma-r2v-{job.work_id}", daemon=True)
            record["thread"] = t
            self._inflight[job.work_id] = record
            t.start()
            return {"work_id": job.work_id, "status": "started"}

    def _record(self, work_id: str, outcome: ExecutionOutcome) -> None:
        with self._lock:
            self._outcomes[work_id] = {
                "attempt_id": outcome.attempt_id,
                "status": outcome.status,
                "detail": outcome.detail,
                "prompt_id": outcome.prompt_id,
                "artifact_ids": outcome.artifact_ids,
                "finished_at": time.time(),
            }
            self._inflight.pop(work_id, None)

    def status(self, work_id: str) -> dict[str, Any]:
        with self._lock:
            inflight = self._inflight.get(work_id)
            if inflight and inflight["thread"].is_alive():
                return {
                    "work_id": work_id,
                    "status": "running" if inflight.get("executing") else "queued",
                }
            if work_id in self._outcomes:
                return dict(self._outcomes[work_id])
        return {"work_id": work_id, "status": "unknown"}

    def wait_for(self, work_id: str, timeout: float = 60) -> dict[str, Any]:
        """Test/CLI helper: block until the inflight thread for work_id ends."""
        with self._lock:
            rec = self._inflight.get(work_id)
        if rec is None:
            return self.status(work_id)
        rec["thread"].join(timeout=timeout)
        return self.status(work_id)


class EnqueueBody(BaseModel):
    chapter_id: str
    ratios: list[Literal["16:9", "9:16"]] = Field(
        default_factory=lambda: ["16:9", "9:16"], min_length=1
    )


def install_run_endpoints(app, worker_builder, registry: RunRegistry) -> None:
    """Attach /api/v1/runs endpoints to a built FastAPI app.

    ``worker_builder`` returns a fresh R2VWorker per run (server process
    stays lock-free; the worker acquires the execution lock per execution).
    """
    from fastapi import HTTPException

    @app.post("/api/v1/runs")
    async def enqueue(request: Request, body: EnqueueBody):
        if not _same_origin(request) or not _csrf_ok(request):
            raise HTTPException(403, "origin/CSRF rejected")
        if worker_builder is None:
            raise HTTPException(
                503, "production R2V transport is not configured; use the qualified probe"
            )
        worker = worker_builder()
        jobs = worker.assemble_jobs(body.chapter_id, body.ratios)
        if not jobs:
            raise HTTPException(404, "no pending motion work for chapter/ratios")
        started = [registry.start(worker, j) for j in jobs]
        return {"enqueued": started}

    @app.get("/api/v1/runs/{work_id}")
    async def run_status(request: Request, work_id: str):
        if not _same_origin(request):
            raise HTTPException(403, "cross-origin request rejected")
        status = registry.status(work_id)
        if status["status"] == "unknown" and worker_builder is not None:
            worker = worker_builder()
            with worker.store.db.transaction() as conn:
                work = conn.execute(
                    "SELECT state, blocked_reason FROM work_items WHERE id=?", (work_id,)
                ).fetchone()
                attempt = conn.execute(
                    "SELECT id, state, remote_prompt_id FROM attempts WHERE work_id=? "
                    "ORDER BY attempt_number DESC LIMIT 1",
                    (work_id,),
                ).fetchone()
            if work is not None:
                return {
                    "work_id": work_id,
                    "status": work["state"],
                    "detail": work["blocked_reason"],
                    "attempt": dict(attempt) if attempt else None,
                }
        return status

    @app.post("/api/v1/attempts/{attempt_id}/reconcile")
    async def reconcile(request: Request, attempt_id: str):
        if not _same_origin(request) or not _csrf_ok(request):
            raise HTTPException(403, "origin/CSRF rejected")
        if worker_builder is None:
            raise HTTPException(
                503, "production R2V transport is not configured; use the qualified probe"
            )
        worker = worker_builder()
        try:
            from starlette.concurrency import run_in_threadpool

            def recover():
                try:
                    return worker.reconcile_uncertain(attempt_id, worker.owner_epoch)
                finally:
                    worker.store.db.close()

            out = await run_in_threadpool(recover)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (InvalidTransition, RevisionConflict) as exc:
            raise HTTPException(409, str(exc)) from exc
        return {
            "attempt_id": out.attempt_id,
            "status": out.status,
            "prompt_id": out.prompt_id,
            "detail": out.detail,
        }


def _same_origin(request) -> bool:
    return _origin_allowed(request)


def _csrf_ok(request) -> bool:
    checker = getattr(request.app.state, "csrf_ok", None)
    return bool(checker and checker(request))


__all__ = ["RunRegistry", "install_run_endpoints"]
