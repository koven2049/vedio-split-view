from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from video_split.config import get_settings
from video_split.database import get_db, init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from video_split.logging_setup import setup_logging
    setup_logging()

    await init_db()

    async for db in get_db():
        from video_split.service.auth_service import ensure_admin_user
        await ensure_admin_user(db)
        break

    yield


def create_app(*, use_lifespan: bool = True) -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Video Split View",
        version="0.1.0",
        lifespan=lifespan if use_lifespan else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.app.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # The public entry rides a cross-border cloudflared tunnel (T28: tunnel
    # latency dominates, app serves in 1-2ms) — compress JSON/HTML so fewer
    # bytes cross the slow leg. Browsers/CF send Accept-Encoding anyway.
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

    from video_split.api.admin import router as admin_router
    from video_split.api.analysis import router as analysis_router
    from video_split.api.api_keys import router as api_keys_router
    from video_split.api.apidocs import router as apidocs_router
    from video_split.api.auth import router as auth_router
    from video_split.api.bilibili import router as bilibili_router
    from video_split.api.debug import router as debug_router
    from video_split.api.feishu_hook import router as feishu_hook_router
    from video_split.api.mindmap import router as mindmap_router
    from video_split.api.public_notes import router as public_notes_router
    from video_split.api.tags import router as tags_router
    from video_split.api.tasks import router as tasks_router
    from video_split.api.videos import router as videos_router
    from video_split.api.youtube import router as youtube_router

    app.include_router(auth_router)
    app.include_router(tasks_router)
    app.include_router(analysis_router)
    app.include_router(videos_router)
    app.include_router(mindmap_router)
    app.include_router(tags_router)
    app.include_router(bilibili_router)
    app.include_router(youtube_router)
    app.include_router(admin_router)
    app.include_router(api_keys_router)
    app.include_router(apidocs_router)
    app.include_router(debug_router)
    app.include_router(feishu_hook_router)
    app.include_router(public_notes_router)

    @app.middleware("http")
    async def log_requests(request: Request, call_next):  # type: ignore[no-untyped-def]
        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = (time.monotonic() - start) * 1000
        if not request.url.path.startswith("/api/health") and request.url.path != "/health":
            logger.info(
                "[api] %s %s → %d (%.0fms)",
                request.method, request.url.path, response.status_code, elapsed_ms,
            )
        return response

    @app.get("/api/health")
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.middleware("http")
    async def cache_thumbnails(request, call_next):  # type: ignore[no-untyped-def]
        """Browser-cacheable, CDN-uncacheable thumbnails.

        Thumbnail files are content-stable (re-analysis reuses the cached
        file), so browsers may keep them for a day; ``private`` keeps shared
        CDNs like Cloudflare out, preserving the original intent.
        """
        response = await call_next(request)
        if request.url.path.startswith("/api/thumbnails/"):
            response.headers["Cache-Control"] = "private, max-age=86400"
        return response

    thumb_dir = Path(settings.storage.temp_dir).parent / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/api/thumbnails", StaticFiles(directory=str(thumb_dir)), name="thumbnails")

    _mount_frontend(app)

    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built SPA from the API process (process-mode deployment).

    Replaces the nginx frontend container: hashed /assets get immutable
    caching, everything else falls back to index.html with no-cache (SPA
    routing). Mounted AFTER all API routes; the fallback explicitly skips
    /api and the OpenAPI docs prefixes so unknown API paths keep their
    404 JSON instead of being swallowed by HTML.
    """
    if not get_settings().app.serve_frontend:
        return
    dist = Path(os.environ.get("VSLP_FRONTEND_DIST", "")) if os.environ.get("VSLP_FRONTEND_DIST") \
        else Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        return

    class _ImmutableStatic(StaticFiles):
        def file_response(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            response = super().file_response(*args, **kwargs)
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            return response

    app.mount("/assets", _ImmutableStatic(directory=str(dist / "assets")), name="assets")

    # Path params arrive WITHOUT the leading slash.
    _skip_prefixes = ("api/", "docs", "redoc", "openapi.json")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        from fastapi import HTTPException
        from fastapi.responses import FileResponse

        if full_path == "api" or full_path.startswith(_skip_prefixes):
            raise HTTPException(status_code=404, detail="Not Found")
        target = dist / full_path
        if full_path and target.is_file():
            return FileResponse(target, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
        return FileResponse(
            dist / "index.html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
        )


app = create_app()
