from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from video_split.database import get_db
from video_split.dependencies import get_current_user, require_user, require_user_or_admin
from video_split.models import Tag, User, Video, video_tags
from video_split.schemas import VideoListOut, VideoOut, VideoUpdate, TagOut
from video_split.service.data_sync import EXPORTS_DIR, _export_filename

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/videos", tags=["videos"])


def _video_to_list_out(v: Video) -> VideoListOut:
    return VideoListOut(
        id=v.id, url=v.url, platform=v.platform, title=v.title,
        thumbnail_url=v.thumbnail_url, duration_seconds=v.duration_seconds,
        is_public=v.is_public, created_at=v.created_at,
        tags=[TagOut.model_validate(t) for t in v.tags],
        owner_name=v.owner.username if v.owner else "",
    )


def _video_to_out(v: Video) -> VideoOut:
    from video_split.schemas import SegmentOut
    return VideoOut(
        id=v.id, url=v.url, platform=v.platform, video_id=v.video_id,
        title=v.title, thumbnail_url=v.thumbnail_url, upload_date=v.upload_date,
        duration_seconds=v.duration_seconds, summary=v.summary, summary_en=v.summary_en,
        usage_json=v.usage_json,
        mindmap_json=v.mindmap_json,
        is_public=v.is_public, created_at=v.created_at, updated_at=v.updated_at,
        segments=[SegmentOut.model_validate(s) for s in v.segments],
        tags=[TagOut.model_validate(t) for t in v.tags],
        owner_name=v.owner.username if v.owner else "",
    )


@router.get("", response_model=list[VideoListOut])
async def list_my_videos(
    q: str = Query("", max_length=128),
    tag: str = Query("", max_length=64),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(require_user_or_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Video)
        .options(selectinload(Video.tags), selectinload(Video.owner))
    )
    if user.role != "admin":
        stmt = stmt.where(Video.user_id == user.id)
    if q:
        stmt = stmt.where(Video.title.ilike(f"%{q}%"))
    if tag:
        stmt = stmt.join(video_tags).join(Tag).where(Tag.name == tag)
    stmt = stmt.order_by(Video.created_at.desc()).offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(stmt)
    videos = result.scalars().unique().all()
    return [_video_to_list_out(v) for v in videos]


@router.get("/public", response_model=list[VideoListOut])
async def list_public_videos(
    q: str = Query("", max_length=128),
    tag: str = Query("", max_length=64),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(Video)
        .options(selectinload(Video.tags), selectinload(Video.owner))
        .where(Video.is_public == True)  # noqa: E712
    )
    if q:
        stmt = stmt.where(Video.title.ilike(f"%{q}%"))
    if tag:
        stmt = stmt.join(video_tags).join(Tag).where(Tag.name == tag)
    stmt = stmt.order_by(Video.created_at.desc()).offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(stmt)
    videos = result.scalars().unique().all()
    return [_video_to_list_out(v) for v in videos]


@router.get("/usage-summary")
async def usage_summary(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregate ASR seconds and LLM tokens across all user videos, grouped by model."""
    result = await db.execute(
        select(Video.usage_json).where(Video.user_id == user.id, Video.usage_json != "")
    )
    asr_totals: dict[str, float] = {}
    llm_totals: dict[str, dict[str, int]] = {}

    for (raw,) in result.all():
        try:
            d = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        asr_model = d.get("asr_model", "")
        if asr_model and d.get("asr_duration_seconds", 0) > 0:
            asr_totals[asr_model] = asr_totals.get(asr_model, 0) + d["asr_duration_seconds"]
        llm_model = d.get("llm_model", "")
        if llm_model and d.get("llm_total_tokens", 0) > 0:
            acc = llm_totals.setdefault(llm_model, {"prompt": 0, "completion": 0, "total": 0})
            acc["prompt"] += d.get("llm_prompt_tokens", 0)
            acc["completion"] += d.get("llm_completion_tokens", 0)
            acc["total"] += d.get("llm_total_tokens", 0)

    return {
        "asr": [{"model": m, "total_seconds": round(s, 1)} for m, s in sorted(asr_totals.items())],
        "llm": [{"model": m, "prompt_tokens": v["prompt"], "completion_tokens": v["completion"],
                 "total_tokens": v["total"]} for m, v in sorted(llm_totals.items())],
    }


@router.get("/{video_id}", response_model=VideoOut)
async def get_video(
    video_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Video)
        .options(selectinload(Video.segments), selectinload(Video.tags), selectinload(Video.owner))
        .where(Video.id == video_id)
    )
    video = result.scalar_one_or_none()
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")
    if user.role == "viewer" and not video.is_public:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    if video.user_id != user.id and not video.is_public and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    return _video_to_out(video)


@router.put("/{video_id}", response_model=VideoOut)
async def update_video(
    video_id: int,
    body: VideoUpdate,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Video)
        .options(selectinload(Video.segments), selectinload(Video.tags), selectinload(Video.owner))
        .where(Video.id == video_id, Video.user_id == user.id)
    )
    video = result.scalar_one_or_none()
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")
    if body.title is not None:
        video.title = body.title
    if body.summary is not None:
        video.summary = body.summary
    await db.commit()
    await db.refresh(video)
    return _video_to_out(video)


@router.delete("/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_video(
    video_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Video).where(Video.id == video_id, Video.user_id == user.id)
    )
    video = result.scalar_one_or_none()
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")
    export_path = EXPORTS_DIR / _export_filename(video.platform, video.video_id)
    await db.delete(video)
    await db.commit()
    if export_path.exists():
        try:
            export_path.unlink()
        except OSError:
            logger.warning("[delete] Failed to remove export file %s", export_path)


@router.post("/bulk-share")
async def bulk_share_videos(
    body: dict,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Set is_public for multiple videos at once. Body: { ids: [1,2,3], share: true }"""
    video_ids: list[int] = body.get("ids", [])
    share: bool = body.get("share", True)
    if not video_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ids list required")
    result = await db.execute(
        select(Video).where(Video.id.in_(video_ids), Video.user_id == user.id)
    )
    videos = result.scalars().all()
    count = 0
    for v in videos:
        if v.is_public != share:
            v.is_public = share
            count += 1
    await db.commit()
    action = "shared" if share else "unshared"
    return {"message": f"{count} video(s) {action}", "affected": count}


@router.post("/{video_id}/share")
async def share_video(
    video_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Video).where(Video.id == video_id, Video.user_id == user.id)
    )
    video = result.scalar_one_or_none()
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")
    video.is_public = True
    await db.commit()
    return {"message": "Video shared to public"}


@router.post("/{video_id}/unshare")
async def unshare_video(
    video_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Video).where(Video.id == video_id, Video.user_id == user.id)
    )
    video = result.scalar_one_or_none()
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")
    video.is_public = False
    await db.commit()
    return {"message": "Video removed from public"}


@router.get("/{video_id}/subtitles")
async def get_segment_subtitles(
    video_id: int,
    start: float = Query(..., description="Segment start seconds"),
    end: float = Query(..., description="Segment end seconds"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return subtitle entries within a time range for a video."""
    result = await db.execute(select(Video).where(Video.id == video_id))
    video = result.scalar_one_or_none()
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")
    if user.role == "viewer" and not video.is_public:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")
    if video.user_id != user.id and not video.is_public and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied")

    if not video.subtitle_json:
        return []

    entries = json.loads(video.subtitle_json)
    return [
        e for e in entries
        if e["start"] >= start - 0.5 and e["start"] < end
    ]
