"""Local chapter crop queue and revision-fenced human review endpoints."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from ..application.crop_jobs import (
    confirm_page,
    enqueue_chapter,
    enqueue_page,
    local_client,
    process_pending,
)
from ..application.crops import replay_page
from ..crops import Annotation
from ..storage.crop_store import CropStore
from ..storage.domain_store import KomaStore
from ..storage.errors import BlockedPrerequisite, NotFound, RevisionConflict


class ReworkBody(BaseModel):
    expected_revision: int = Field(ge=1)
    panel_ids: list[str]
    note: str = Field(default="", max_length=4000)


class ConfirmBody(BaseModel):
    expected_revision: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=200)
    inspected_masks: bool = False


class EditBody(BaseModel):
    expected_revision: int = Field(ge=1)
    reading_order: list[str]
    owners: dict[str, str | None]


class CropRunner:
    """One inference thread; durable requests/results live in crop_jobs, not here."""

    def __init__(self, data_dir: Path, client_factory=local_client):
        self.data_dir = data_dir
        self.client_factory = client_factory
        self.lock = threading.Lock()
        self.thread = None
        self.error = ""

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return "running"
            self.error = ""
            self.thread = threading.Thread(target=self._run, daemon=True, name="koma-crop-worker")
            self.thread.start()
        return "started"

    def _run(self):
        from ..cli import _owner

        store = KomaStore.open(self.data_dir)
        try:
            with _owner("crop-chapter", self.data_dir):
                # Drain newly queued pages too; a resume button covers the shutdown race.
                while process_pending(store, client_factory=self.client_factory):
                    pass
        except BaseException as exc:
            self.error = f"Worker stopped ({type(exc).__name__}): {exc}. Resume when available."
        finally:
            store.db.close()


def install_crop_endpoints(app, data_dir: Path, *, client_factory=local_client):
    from .api import _origin_allowed

    runner = CropRunner(data_dir, client_factory)
    app.state.crop_runner = runner

    def mutate(request):
        if not _origin_allowed(request) or not app.state.csrf_ok(request):
            raise HTTPException(403, "origin/CSRF rejected")

    def page_row(store, page_id):
        with store.db.transaction() as conn:
            row = conn.execute(
                "SELECT p.*,s.slug,c.number AS chapter FROM pages p "
                "JOIN series s ON s.id=p.series_id JOIN chapters c ON c.id=p.chapter_id "
                "WHERE p.id=? AND p.deleted=0",
                (page_id,),
            ).fetchone()
        if row is None:
            raise NotFound("crop page not found")
        return dict(row)

    def run(fn):
        store = KomaStore.open(data_dir)
        try:
            return fn(store)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except RevisionConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except BlockedPrerequisite as exc:
            raise HTTPException(422, str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            store.db.close()

    @app.get("/crops", response_class=HTMLResponse)
    def crop_ui():
        return (Path(__file__).parent / "static" / "crops.html").read_text()

    @app.get("/api/v1/crops/chapters")
    def chapters():
        def query(store):
            with store.db.transaction() as conn:
                return {
                    "chapters": [
                        dict(r)
                        for r in conn.execute(
                            "SELECT s.slug,c.number,COUNT(p.id) AS pages FROM chapters c "
                            "JOIN series s ON s.id=c.series_id "
                            "LEFT JOIN pages p ON p.chapter_id=c.id "
                            "AND p.deleted=0 GROUP BY c.id ORDER BY s.slug,c.number"
                        )
                    ]
                }

        return run(query)

    @app.get("/api/v1/crops/chapters/{slug}/{chapter}")
    def chapter_view(slug: str, chapter: int):
        def query(store):
            pages = []
            for page in store.load_pages(slug, chapter):
                row = CropStore(store.db).latest(page["id"])
                if row is None:
                    continue
                a = Annotation.model_validate_json(row["payload"])
                with store.db.transaction() as conn:
                    jobs = [
                        dict(r)
                        for r in conn.execute(
                            "SELECT * FROM crop_jobs WHERE page_id=? "
                            "ORDER BY created_at DESC,id DESC",
                            (page["id"],),
                        )
                    ]
                pages.append(
                    {
                        "id": page["id"],
                        "number": page["sequence_position"],
                        "revision": row["revision"],
                        "status": row["decision_status"],
                        "source_url": f"/api/v1/crops/pages/{page['id']}/source",
                        "reading_order": a.reading_order,
                        "order_rationale": a.order_rationale,
                        "semantic": a.semantic,
                        "jobs": jobs,
                        "panels": [
                            {
                                "id": pid,
                                "order": i,
                                "needs_review": next(
                                    p.needs_review for p in a.panels if p.id == pid
                                ),
                                "url": (f"/api/v1/crops/pages/{page['id']}/{row['revision']}"
                                        f"/panels/{pid}"),
                            }
                            for i, pid in enumerate(a.reading_order, 1)
                        ],
                        "components": [
                            {
                                "id": c.id,
                                "owner_panel": c.owner_panel,
                                "kind": c.kind,
                                "rationale": c.rationale,
                                "needs_review": c.needs_review,
                            }
                            for c in a.components
                        ],
                    }
                )
            return {"pages": pages, "worker_error": runner.error}

        return run(query)

    @app.post("/api/v1/crops/chapters/{slug}/{chapter}/process")
    def process_chapter(slug: str, chapter: int, request: Request):
        mutate(request)
        jobs = run(lambda store: enqueue_chapter(store, slug, chapter))
        return {"jobs": jobs, "worker": runner.start()}

    @app.post("/api/v1/crops/resume")
    def resume(request: Request):
        mutate(request)
        return {"worker": runner.start()}

    @app.post("/api/v1/crops/pages/{page_id}/rework")
    def rework(page_id: str, body: ReworkBody, request: Request):
        mutate(request)
        job = run(
            lambda store: enqueue_page(
                store, page_id, body.panel_ids, body.expected_revision, body.note
            )
        )
        return {"job": job, "worker": runner.start()}

    @app.post("/api/v1/crops/pages/{page_id}/approve")
    def approve(page_id: str, body: ConfirmBody, request: Request):
        mutate(request)
        if not body.inspected_masks:
            raise HTTPException(400, "confirm visual inspection of masks, overfill and ownership")

        def action(store):
            page = page_row(store, page_id)
            revision = confirm_page(store, page_id, body.expected_revision, body.actor)
            replay_page(store, page["slug"], page["chapter"], page_id)
            return {"revision": revision, "status": "approved"}

        return run(action)

    @app.post("/api/v1/crops/pages/{page_id}/decisions")
    def edit(page_id: str, body: EditBody, request: Request):
        mutate(request)

        def action(store):
            page = page_row(store, page_id)
            row = CropStore(store.db).latest(page_id)
            if row is None:
                raise NotFound("no annotations")
            a = Annotation.model_validate_json(row["payload"])
            if set(body.owners) != {c.id for c in a.components}:
                raise ValueError("provide ownership for every displayed component")
            a.reading_order = body.reading_order
            a.order_needs_review = True
            for c in a.components:
                c.owner_panel = body.owners[c.id]
                c.needs_review = True
                c.rationale = "Human ownership edit; inspect updated preview"
            revision = CropStore(store.db).submit(
                page_id, a, expected_revision=body.expected_revision
            )
            replay_page(store, page["slug"], page["chapter"], page_id)
            return {"revision": revision}

        return run(action)

    @app.get("/api/v1/crops/pages/{page_id}/source")
    def source(page_id: str):
        def action(store):
            page = page_row(store, page_id)
            root = (data_dir / "series" / page["slug"] / "source").resolve()
            path = (root / page["source_path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise NotFound("source unavailable")
            return FileResponse(path, headers={"Cache-Control": "no-store"})

        return run(action)

    @app.get("/api/v1/crops/pages/{page_id}/{revision}/panels/{panel_id}")
    def panel_image(page_id: str, revision: int, panel_id: str):
        def action(store):
            page = page_row(store, page_id)
            with store.db.transaction() as conn:
                row = conn.execute(
                    "SELECT * FROM crop_annotations WHERE page_id=? AND revision=?",
                    (page_id, revision),
                ).fetchone()
            if row is None:
                raise NotFound("crop revision unavailable")
            a = Annotation.model_validate_json(row["payload"])
            if panel_id not in a.reading_order:
                raise NotFound("panel not on this page")
            order = a.reading_order.index(panel_id) + 1
            path = (
                data_dir
                / "series"
                / page["slug"]
                / "chapters"
                / f"{page['chapter']:02d}"
                / "panels/masks"
                / Path(page["source_path"]).name
                / row["annotation_hash"]
                / f"{order:03d}_{panel_id}.png"
            )
            if not path.is_file():
                raise NotFound("crop preview not exported; replay this page")
            return FileResponse(path, media_type="image/png")

        return run(action)
