from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from video_split.database import get_db
from video_split.dependencies import get_current_user, require_admin
from video_split.models import User, Video

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/videos", tags=["mindmap"])


async def _get_video(video_id: int, db: AsyncSession) -> Video:
    # Any authenticated account (admin or viewer) can read any video's mindmap;
    # callers gate write access via require_admin.
    result = await db.execute(
        select(Video)
        .options(selectinload(Video.segments))
        .where(Video.id == video_id)
    )
    video = result.scalar_one_or_none()
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")
    return video


@router.get("/{video_id}/mindmap")
async def get_mindmap(
    video_id: int,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return existing mindmap data, or status not_generated."""
    video = await _get_video(video_id, db)
    if video.mindmap_json:
        try:
            return json.loads(video.mindmap_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning("[mindmap] Corrupt mindmap_json for video %d, treating as not generated", video_id)
            return {"status": "not_generated"}
    return {"status": "not_generated"}


@router.post("/{video_id}/mindmap")
async def generate_mindmap_endpoint(
    video_id: int,
    refresh: bool = False,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Generate mindmap via SSE stream. Admin only."""
    video = await _get_video(video_id, db)

    from video_split.service.brainstorm import generate_mindmap

    if video.mindmap_json and not refresh:
        cached = json.loads(video.mindmap_json)

        async def cached_stream():
            yield {"data": json.dumps({"stage": "complete", "progress": 100, "data": cached}, ensure_ascii=False)}

        return EventSourceResponse(cached_stream())

    async def event_stream():
        async for event in generate_mindmap(video.id, db, usage_user_id=user.id):
            yield {"data": json.dumps(event, ensure_ascii=False)}

    return EventSourceResponse(event_stream())
