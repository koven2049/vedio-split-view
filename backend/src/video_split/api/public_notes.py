"""Unauthenticated read of a completed note, gated by HMAC view signature."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from video_split.database import get_db
from video_split.models import Video
from video_split.schemas import PublicNoteOut, PublicNoteSegmentOut
from video_split.share_token import verify_view_sig

router = APIRouter(tags=["public"])


@router.get("/api/public/notes/{video_id}", response_model=PublicNoteOut)
async def public_note(
    video_id: int,
    sig: str = Query(description="HMAC view signature from the Feishu result card"),
    db: AsyncSession = Depends(get_db),
):
    """Full analysis text for a signed Feishu share link. No login required."""
    if not verify_view_sig(video_id, sig):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    result = await db.execute(
        select(Video).options(selectinload(Video.segments)).where(Video.id == video_id)
    )
    video = result.scalar_one_or_none()
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return PublicNoteOut(
        id=video.id,
        title=video.title or "",
        url=video.url,
        platform=video.platform,
        duration_seconds=video.duration_seconds,
        summary=video.summary or "",
        essence=video.essence or "",
        transcript=video.raw_transcript or "",
        segments=[
            PublicNoteSegmentOut(
                segment_index=s.segment_index,
                title=s.title or "",
                summary=s.summary or "",
                start_seconds=s.start_seconds,
                end_seconds=s.end_seconds,
            )
            for s in video.segments
        ],
    )
