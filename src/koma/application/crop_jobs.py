"""Durable chapter crop jobs, sequential local inference, and selective rework."""

from __future__ import annotations

import json

from ..clients import OpenAICompatClient
from ..config import settings
from ..crops import Annotation
from ..steps.crop_semantics import adjudicate
from ..storage.crop_store import CropStore
from ..storage.errors import BlockedPrerequisite, NotFound, RevisionConflict
from ..util import new_id, utcnow_iso
from .crops import replay_page


def local_client():
    config = settings()
    if not config.vision_base_url:
        raise ValueError("Configure KOMA_VISION_BASE_URL for the existing local VLM")
    return OpenAICompatClient(base_url=config.vision_base_url, timeout=180), config.vision_model


def enqueue_page(
    store, page_id: str, selected: list[str], expected_revision: int, note: str = ""
) -> dict:
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("select distinct panels for rework")
    if len(note) > 4000:
        raise ValueError("rework note is too long")
    with store.db.transaction(immediate=True) as conn:
        latest = conn.execute(
            "SELECT * FROM crop_annotations WHERE page_id=? ORDER BY revision DESC LIMIT 1",
            (page_id,),
        ).fetchone()
        if latest is None:
            raise NotFound("page has no crop proposals; ingest it first")
        if latest["revision"] != expected_revision:
            raise RevisionConflict("page changed since review; refresh before rework")
        ids = json.loads(latest["payload"])["reading_order"]
        if not set(selected) <= set(ids):
            raise ValueError("selected panel is not on this page")
        active = conn.execute(
            "SELECT * FROM crop_jobs WHERE page_id=? AND state IN ('pending','running')",
            (page_id,),
        ).fetchone()
        if active:
            if (
                json.loads(active["selected_panels"]) == selected
                and active["note"] == note
                and active["expected_revision"] == expected_revision
            ):
                return dict(active)
            raise RevisionConflict("this page already has an active crop request")
        now, job_id = utcnow_iso(), new_id()
        conn.execute(
            "INSERT INTO crop_jobs (id,page_id,expected_revision,selected_panels,note,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (job_id, page_id, expected_revision, json.dumps(selected), note, now, now),
        )
        return dict(conn.execute("SELECT * FROM crop_jobs WHERE id=?", (job_id,)).fetchone())


def enqueue_chapter(store, slug: str, chapter: int) -> list[dict]:
    pages = store.load_pages(slug, chapter)
    if not pages:
        raise NotFound("chapter has no pages; import a source folder first")
    jobs = []
    for page in pages:
        row = CropStore(store.db).latest(page["id"])
        if row is None:
            raise NotFound("chapter contains a page without crop proposals")
        annotation = Annotation.model_validate_json(row["payload"])
        # Resume is safe: never rerun successful semantics or approved revisions.
        if row["decision_status"] == "approved" or annotation.semantic.get("job_id"):
            continue
        jobs.append(enqueue_page(store, page["id"], annotation.reading_order, row["revision"]))
    return jobs


def _finish(store, job_id: str, state: str, *, revision=None, error="") -> None:
    with store.db.transaction(immediate=True) as conn:
        conn.execute(
            "UPDATE crop_jobs SET state=?,result_revision=?,error=?,updated_at=? WHERE id=?",
            (state, revision, error[:2000], utcnow_iso(), job_id),
        )


def process_pending(store, *, client_factory=local_client) -> list[dict]:
    """Caller holds the application owner lock. No model calls in transactions.

    Running rows from a stopped worker are recoverable under that exclusive lock.
    An already committed result is replayed, never sent to the VLM again.
    """
    with store.db.transaction(immediate=True) as conn:
        conn.execute("UPDATE crop_jobs SET state='pending' WHERE state='running'")
        jobs = [
            dict(r)
            for r in conn.execute(
                "SELECT j.*,p.source_path,p.series_id,c.number AS chapter,s.slug "
                "FROM crop_jobs j JOIN pages p ON p.id=j.page_id "
                "JOIN chapters c ON c.id=p.chapter_id JOIN series s ON s.id=p.series_id "
                "WHERE j.state='pending' ORDER BY j.created_at,j.id"
            )
        ]
    outcomes = []
    client = None
    client_error = None
    try:
        for job in jobs:
            try:
                with store.db.transaction(immediate=True) as conn:
                    claimed = conn.execute(
                        "UPDATE crop_jobs SET state='running',updated_at=? "
                        "WHERE id=? AND state='pending'",
                        (utcnow_iso(), job["id"]),
                    ).rowcount
                if not claimed:
                    continue
                row = CropStore(store.db).latest(job["page_id"])
                if row is None:
                    raise NotFound("crop annotation disappeared")
                annotation = Annotation.model_validate_json(row["payload"])
                if annotation.semantic.get("job_id") == job["id"]:
                    revision = row["revision"]  # crash after commit, before export/finish
                else:
                    if row["revision"] != job["expected_revision"]:
                        raise RevisionConflict("manual edits superseded this crop request")
                    if client is None:
                        if client_error:
                            raise RuntimeError(client_error)
                        try:
                            client, model = client_factory()
                        except Exception as exc:
                            client_error = str(exc)
                            raise
                    source = (
                        store.db.data_dir / "series" / job["slug"] / "source" / job["source_path"]
                    )
                    from ..steps.crop_proposals import add_overfill_candidates

                    if not annotation.semantic.get("job_id"):
                        annotation = add_overfill_candidates(source, annotation)
                    else:
                        annotation = add_overfill_candidates(
                            source, annotation, selected=json.loads(job["selected_panels"]),
                            refinement_key=job["id"],
                        )
                    result = adjudicate(
                        source,
                        annotation,
                        client,
                        model,
                        selected_panels=json.loads(job["selected_panels"]),
                        note=job["note"],
                        job_id=job["id"],
                    )
                    revision = CropStore(store.db).submit(
                        job["page_id"], result, expected_revision=job["expected_revision"]
                    )
                replay_page(store, job["slug"], job["chapter"], job["page_id"])
                _finish(store, job["id"], "completed", revision=revision)
                outcomes.append({"id": job["id"], "state": "completed", "revision": revision})
            except Exception as exc:
                state = "conflict" if isinstance(exc, RevisionConflict) else "failed"
                _finish(store, job["id"], state, error=str(exc))
                outcomes.append({"id": job["id"], "state": state, "error": str(exc)[:2000]})
    finally:
        if client is not None:
            client.close()
    return outcomes


def confirm_page(store, page_id: str, expected_revision: int, actor: str) -> int:
    """Explicit web confirmation of all visible masks/ownership/order on this page."""
    if not actor.strip():
        raise ValueError("reviewer is required")
    row = CropStore(store.db).latest(page_id)
    if row is None:
        raise NotFound("crop page not found")
    if row["revision"] != expected_revision:
        raise RevisionConflict("crop page changed; refresh before approval")
    with store.db.transaction() as conn:
        active = conn.execute(
            "SELECT 1 FROM crop_jobs WHERE page_id=? AND state IN ('pending','running')",
            (page_id,),
        ).fetchone()
    if active:
        raise BlockedPrerequisite("wait for pending rework before reviewing")
    annotation = Annotation.model_validate_json(row["payload"])
    if any(c.owner_panel is None for c in annotation.components):
        raise BlockedPrerequisite("unassigned objects require rework or a manual ownership edit")
    annotation.order_needs_review = False
    for item in [*annotation.panels, *annotation.components]:
        item.needs_review = False
    annotation.semantic = {**annotation.semantic,
                           "human_review": {"actor": actor, "reviewed_at": utcnow_iso()}}
    crops = CropStore(store.db)
    revision = crops.submit(page_id, annotation, expected_revision=expected_revision)
    crops.approve(page_id, expected_revision=revision, actor=actor)
    return revision
