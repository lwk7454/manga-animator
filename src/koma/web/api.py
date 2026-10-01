"""R2 — local FastAPI service under /api/v1 (D17).

Same-origin local app: loopback bind by default, origin/host checks, CSRF
token for mutations, expected-revision CAS on mutations, media served by
artifact ID only (never arbitrary paths). The browser never talks to ComfyUI.
"""

from __future__ import annotations

import hmac
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from ..application.assets import AppContext, AssetService
from ..storage.errors import (
    BlockedPrerequisite,
    DuplicateOperation,
    InvalidTransition,
    NotFound,
    RevisionConflict,
)

SESSION_COOKIE = "koma_csrf"


def _origin_allowed(request: Request) -> bool:
    """Loopback-only default: same-origin requests pass; cross-origin blocked."""
    from urllib.parse import urlsplit

    def authority(url):
        parsed = urlsplit(url)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            return None
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return None
        if parsed.scheme not in {"http", "https"}:
            return None
        return (
            parsed.scheme,
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )

    try:
        target = authority(f"{request.url.scheme}://{request.headers.get('host', '')}")
        origin = request.headers.get("origin")
        return target is not None and (origin is None or authority(origin) == target)
    except ValueError:
        return False


def _csrf_ok(request: Request) -> bool:
    cookie = request.cookies.get(SESSION_COOKIE)
    header = request.headers.get("x-koma-csrf")
    if not cookie or not header:
        return False
    return hmac.compare_digest(cookie, header)


class ApproveBody(BaseModel):
    revision: int = Field(gt=0)
    approved_by: str = Field(min_length=1)
    expected_asset_revision: int = Field(gt=0)
    note: str = ""


class RejectBody(BaseModel):
    revision: int = Field(gt=0)
    note: str = ""


class BindBody(BaseModel):
    asset_id: str
    asset_revision: int = Field(gt=0)
    panel_id: str | None = None
    shot_id: str | None = None


class UnknownBody(BaseModel):
    panel_id: str | None = None
    shot_id: str | None = None
    note: str = ""


class AppFactory:
    """Builds the FastAPI app against one data root (same-origin web app)."""

    def __init__(self, data_dir: Path, *, worker_builder=None):
        self.worker_builder = worker_builder
        self.ctx = AppContext.open(data_dir)
        self.assets = AssetService(self.ctx)

    def build(self) -> FastAPI:
        app = FastAPI(title="koma", version="0.1.0", docs_url="/api/docs")
        # No cross-origin by default: loopback same-origin only. LAN exposure is
        # a separate explicit configuration with auth (PLAN §D).
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[],
            allow_credentials=False,
            allow_methods=[],
            allow_headers=[],
        )

        @app.middleware("http")
        async def local_origin(request: Request, call_next):
            from fastapi.responses import JSONResponse

            if not _origin_allowed(request):
                return JSONResponse({"detail": "host/origin rejected"}, status_code=403)
            return await call_next(request)

        # ── error mapping: typed store errors → HTTP ─────────────────────
        import functools

        def map_errors(fn):
            @functools.wraps(fn)
            async def wrapper(*a, **kw):
                try:
                    return await fn(*a, **kw)
                except NotFound as e:
                    raise HTTPException(404, str(e)) from e
                except (InvalidTransition, DuplicateOperation) as e:
                    raise HTTPException(409, str(e)) from e
                except RevisionConflict as e:
                    raise HTTPException(409, str(e)) from e
                except BlockedPrerequisite as e:
                    raise HTTPException(422, str(e)) from e
                except (ValueError, TypeError) as e:
                    raise HTTPException(400, str(e)) from e

            return wrapper

        # ── schema (models at module level; see ApproveBody etc.) ────────

        @app.get("/api/v1/health")
        async def health():
            return {"ok": True}

        @app.get("/api/v1/csrf")
        async def csrf(response: Response):
            token = secrets.token_urlsafe(32)
            response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict")
            return {"token": token}

        @app.get("/api/v1/series/{series_id}/assets")
        @map_errors
        async def list_assets(series_id: str, request: Request):
            if not _origin_allowed(request):
                raise HTTPException(403, "cross-origin request rejected")
            return {"assets": self.assets.list_assets(series_id)}

        @app.post("/api/v1/assets/{asset_id}/revisions")
        @map_errors
        async def propose(asset_id: str, request: Request, body: dict):
            if not _origin_allowed(request):
                raise HTTPException(403, "cross-origin request rejected")
            if not _csrf_ok(request):
                raise HTTPException(403, "CSRF token missing/invalid")
            payload = body.get("payload")
            if not isinstance(payload, dict):
                raise HTTPException(400, "payload object required")
            return self.assets.propose_revision(
                asset_id,
                payload,
                origin=body.get("origin", "generated"),
                artifact_ids=body.get("artifact_ids"),
            )

        @app.post("/api/v1/assets/{asset_id}/approve")
        @map_errors
        async def approve(asset_id: str, request: Request, body: ApproveBody):
            if not _origin_allowed(request) or not _csrf_ok(request):
                raise HTTPException(403, "origin/CSRF rejected")
            return self.assets.approve(
                asset_id,
                body.revision,
                approved_by=body.approved_by,
                expected_asset_revision=body.expected_asset_revision,
                note=body.note if hasattr(body, "note") else "",
            )

        @app.post("/api/v1/assets/{asset_id}/reject")
        @map_errors
        async def reject(asset_id: str, request: Request, body: RejectBody):
            if not _origin_allowed(request) or not _csrf_ok(request):
                raise HTTPException(403, "origin/CSRF rejected")
            return self.assets.reject(asset_id, body.revision, note=body.note)

        @app.post("/api/v1/assets/{asset_id}/revoke")
        @map_errors
        async def revoke(asset_id: str, request: Request, body: RejectBody):
            if not _origin_allowed(request) or not _csrf_ok(request):
                raise HTTPException(403, "origin/CSRF rejected")
            return self.assets.revoke(asset_id, body.revision, note=body.note)

        @app.get("/api/v1/artifacts/{artifact_id}")
        async def artifact(artifact_id: str):
            from fastapi.responses import FileResponse

            with self.ctx.db.transaction() as conn:
                row = conn.execute(
                    "SELECT relative_path FROM artifacts WHERE id=?", (artifact_id,)
                ).fetchone()
            if row is None:
                raise HTTPException(404, "artifact not found")
            root = self.ctx.data_dir.resolve()
            relative = Path(row["relative_path"])
            path = (root / relative).resolve()
            if relative.is_absolute() or not path.is_relative_to(root) or not path.is_file():
                raise HTTPException(404, "artifact unavailable")
            types = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".mp4": "video/mp4",
            }
            media_type = types.get(path.suffix.lower())
            if not media_type:
                raise HTTPException(415, "unsupported preview media")
            return FileResponse(
                path, media_type=media_type, headers={"X-Content-Type-Options": "nosniff"}
            )

        @app.get("/api/v1/assets/{asset_id}/impact")
        @map_errors
        async def impact(asset_id: str, request: Request):
            return self.assets.dependency_impact(asset_id)

        @app.post("/api/v1/series/{series_id}/bindings")
        @map_errors
        async def bind(series_id: str, request: Request, body: BindBody):
            if not _origin_allowed(request) or not _csrf_ok(request):
                raise HTTPException(403, "origin/CSRF rejected")
            if (body.panel_id is None) == (body.shot_id is None):
                raise HTTPException(400, "exactly one panel_id/shot_id required")
            if body.panel_id:
                return self.assets.bind_to_panel(
                    series_id, body.panel_id, body.asset_id, body.asset_revision
                )
            if body.shot_id:
                return self.assets.bind_to_shot(
                    series_id, body.shot_id, body.asset_id, body.asset_revision
                )
            raise HTTPException(400, "panel_id or shot_id required")

        @app.post("/api/v1/series/{series_id}/bindings/unknown")
        @map_errors
        async def bind_unknown(series_id: str, request: Request, body: dict):
            if not _origin_allowed(request) or not _csrf_ok(request):
                raise HTTPException(403, "origin/CSRF rejected")
            return self.assets.mark_unknown(
                series_id, body.get("panel_id"), body.get("shot_id"), body.get("note", "")
            )

        # media endpoints use explicit handlers defined at build time to keep
        # the artifact-ID-only rule; range streaming added in the media module.
        self._install_csrf_helper(app)

        from .assembly import install_assembly_endpoints
        from .runs import RunRegistry, install_run_endpoints
        from .static_mount import install_static

        # Configuration enables execution of already compiled work. It does not
        # qualify visual quality or implicitly create/approve a shot plan.
        worker_builder = self.worker_builder
        if worker_builder is None:
            import os

            from ..network import require_local_endpoint
            from ..r2v_transport import build_worker

            server = os.environ.get("KOMA_COMFY_BASE")
            if server:
                server = require_local_endpoint(server)

                def worker_builder():
                    return build_worker(self.ctx.db, server)
        install_run_endpoints(app, worker_builder, RunRegistry())

        def assembly_builder():
            from ..shot_assembly import AssemblyService

            return AssemblyService(self.ctx.db, tts_base=self._tts_base())

        install_assembly_endpoints(app, assembly_builder, self.ctx.data_dir)
        from .crops import install_crop_endpoints

        install_crop_endpoints(app, self.ctx.data_dir)
        install_static(app)

        return app

    def _tts_base(self) -> str | None:
        """TTS lane endpoint from the environment (never hardwired)."""
        import os

        return os.environ.get("KOMA_TTS_BASE")

    def _install_csrf_helper(self, app: FastAPI) -> None:
        def _csrf_ok(request: Request) -> bool:
            cookie = request.cookies.get(SESSION_COOKIE)
            header = request.headers.get("x-koma-csrf")
            if not cookie or not header:
                return False
            return hmac.compare_digest(cookie, header)

        app.state.csrf_ok = _csrf_ok


__all__ = ["SESSION_COOKIE", "AppFactory"]
