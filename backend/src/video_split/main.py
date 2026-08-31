from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
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

    from video_split.api.admin import router as admin_router
    from video_split.api.analysis import router as analysis_router
    from video_split.api.api_keys import router as api_keys_router
    from video_split.api.apidocs import router as apidocs_router
    from video_split.api.auth import router as auth_router
    from video_split.api.bilibili import router as bilibili_router
    from video_split.api.debug import router as debug_router
    from video_split.api.tags import router as tags_router
    from video_split.api.tasks import router as tasks_router
    from video_split.api.videos import router as videos_router
    from video_split.api.youtube import router as youtube_router
    from video_split.api.mindmap import router as mindmap_router
    from video_split.api.feishu_hook import router as feishu_hook_router
    from video_split.api.public_notes import router as public_notes_router

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
    async def no_cache_thumbnails(request, call_next):  # type: ignore[no-untyped-def]
        """Prevent Cloudflare from caching stale thumbnail responses."""
        response = await call_next(request)
        if request.url.path.startswith("/api/thumbnails/"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    thumb_dir = Path(settings.storage.temp_dir).parent / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/api/thumbnails", StaticFiles(directory=str(thumb_dir)), name="thumbnails")

    return app


app = create_app()
