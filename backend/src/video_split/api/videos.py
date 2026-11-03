from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, load_only, selectinload

from video_split.database import get_db
from video_split.dependencies import get_current_user, require_admin, require_authenticated
from video_split.models import Segment, Tag, User, Video, video_tags
from video_split.schemas import (
    DialogueOut,
    LLMLogOut,
    TagOut,
    TranscriptOut,
    TranscriptSegment,
    VideoIdsOut,
    VideoListOut,
    VideoMetaOut,
    VideoOut,
    VideoUpdate,
)
from video_split.service.data_sync import EXPORTS_DIR, _export_filename
from video_split.service.dialogue import derive_dialogue_view, format_dialogue
from video_split.service.downloader import SubtitleEntry
from video_split.service.video_service import accumulate_usage, merge_llm_usage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/videos", tags=["videos"])

# List rows only render these columns. Loading whole Video rows would also
# pull raw_transcript / subtitle_json (up to ~270 KB per row in prod) that
# list endpoints never read. user_id stays loaded for the owner relationship.
_LIST_COLUMNS = load_only(
    Video.id, Video.user_id, Video.url, Video.platform, Video.title,
    Video.thumbnail_url, Video.duration_seconds, Video.is_public, Video.created_at,
)
_META_COLUMNS = load_only(
    Video.id, Video.user_id, Video.url, Video.platform, Video.video_id, Video.title,
    Video.thumbnail_url, Video.upload_date, Video.uploader, Video.duration_seconds,
    Video.is_public, Video.created_at, Video.updated_at,
)


def _filter_videos(stmt, q: str, tag: str):
    if q:
        stmt = stmt.where(Video.title.ilike(f"%{q}%"))
    if tag:
        stmt = stmt.join(video_tags).join(Tag).where(Tag.name == tag)
    return stmt


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
        uploader=v.uploader,
        duration_seconds=v.duration_seconds, summary=v.summary, summary_en=v.summary_en,
        essence=v.essence,
        usage_json=v.usage_json,
        mindmap_json=v.mindmap_json,
        takeaways_json=v.takeaways_json,
        show_intro=v.show_intro,
        has_dialogue=bool(v.dialogue_json),
        is_public=v.is_public, created_at=v.created_at, updated_at=v.updated_at,
        segments=[SegmentOut.model_validate(s) for s in v.segments],
        tags=[TagOut.model_validate(t) for t in v.tags],
        owner_name=v.owner.username if v.owner else "",
    )


async def _get_accessible_video(db: AsyncSession, video_id: int, user: User, *, load=()) -> Video:
    stmt = select(Video).where(Video.id == video_id)
    if load:
        stmt = stmt.options(*load)
    result = await db.execute(stmt)
    video = result.scalar_one_or_none()
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")
    # Any authenticated account can read any video: admin owns everything and
    # viewer has full read access to the whole library. Write endpoints gate on
    # require_admin, so no per-video authorization is needed for reads.
    return video


@router.get("", response_model=list[VideoListOut])
async def list_my_videos(
    q: str = Query("", max_length=128),
    tag: str = Query("", max_length=64),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _user: User = Depends(require_authenticated),
    db: AsyncSession = Depends(get_db),
):
    """Page through the library (newest first) with list-card fields.

    Whole library is visible to every logged-in account (admin + viewer).
    Use GET /api/videos/ids to enumerate every id in one call.
    """
    stmt = select(Video).options(_LIST_COLUMNS, selectinload(Video.tags), selectinload(Video.owner))
    stmt = _filter_videos(stmt, q, tag)
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
        .options(_LIST_COLUMNS, selectinload(Video.tags), selectinload(Video.owner))
        .where(Video.is_public == True)  # noqa: E712
    )
    stmt = _filter_videos(stmt, q, tag)
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


@router.get("/llm-logs", response_model=list[LLMLogOut])
async def llm_logs(
    _user: User = Depends(require_authenticated),
    db: AsyncSession = Depends(get_db),
):
    """Recent LLM call history (last 100 entries)."""
    from video_split.models import LLMLog
    stmt = select(LLMLog).order_by(LLMLog.created_at.desc()).limit(100)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/ids", response_model=VideoIdsOut)
async def list_video_ids(
    q: str = Query("", max_length=128, description="Title substring filter"),
    tag: str = Query("", max_length=64, description="Exact tag name filter"),
    _user: User = Depends(require_authenticated),
    db: AsyncSession = Depends(get_db),
):
    """Every video id in the library (newest first), unpaged.

    Pair with GET /api/videos/{video_id}/meta for lightweight metadata or
    GET /api/videos/{video_id} for the full detail.
    """
    stmt = _filter_videos(select(Video.id), q, tag).order_by(Video.created_at.desc(), Video.id.desc())
    ids = list((await db.execute(stmt)).scalars().all())
    return VideoIdsOut(total=len(ids), ids=ids)


@router.get("/{video_id}/meta", response_model=VideoMetaOut)
async def get_video_meta(
    video_id: int,
    _user: User = Depends(require_authenticated),
    db: AsyncSession = Depends(get_db),
):
    """Lightweight metadata for one video: title, platform, url, duration,
    uploader, tags, segment count and whether dialogue / mindmap exist.
    No summary or body text — use GET /api/videos/{video_id} for that."""
    segment_count = (
        select(func.count(Segment.id)).where(Segment.video_id == Video.id).scalar_subquery()
    )
    stmt = (
        select(
            Video,
            func.coalesce(func.length(Video.dialogue_json), 0) > 0,
            func.coalesce(func.length(Video.mindmap_json), 0) > 0,
            segment_count,
        )
        .options(_META_COLUMNS, selectinload(Video.tags), selectinload(Video.owner))
        .where(Video.id == video_id)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")
    v, has_dialogue, has_mindmap, n_segments = row
    return VideoMetaOut(
        id=v.id, url=v.url, platform=v.platform, video_id=v.video_id, title=v.title,
        thumbnail_url=v.thumbnail_url, upload_date=v.upload_date, uploader=v.uploader,
        duration_seconds=v.duration_seconds, is_public=v.is_public,
        has_dialogue=bool(has_dialogue), has_mindmap=bool(has_mindmap),
        segment_count=n_segments, tags=[TagOut.model_validate(t) for t in v.tags],
        owner_name=v.owner.username if v.owner else "",
        created_at=v.created_at, updated_at=v.updated_at,
    )


@router.get("/{video_id}", response_model=VideoOut)
async def get_video(
    video_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full detail: summary, essence, takeaways, segments, mindmap JSON, tags.

    Transcript text is not included — fetch it from /transcript, /subtitles
    or /dialogue.
    """
    video = await _get_accessible_video(
        db, video_id, user,
        load=(
            # The detail view never reads these (they have their own endpoints).
            defer(Video.raw_transcript), defer(Video.subtitle_json),
            selectinload(Video.segments), selectinload(Video.tags), selectinload(Video.owner),
        ),
    )
    return _video_to_out(video)


@router.put("/{video_id}", response_model=VideoOut)
async def update_video(
    video_id: int,
    body: VideoUpdate,
    user: User = Depends(require_admin),
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
    user: User = Depends(require_admin),
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
    user: User = Depends(require_admin),
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
    user: User = Depends(require_admin),
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
    user: User = Depends(require_admin),
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


@router.get("/{video_id}/subtitles", response_model=list[TranscriptSegment])
async def get_segment_subtitles(
    video_id: int,
    start: float = Query(..., description="Segment start seconds"),
    end: float = Query(..., description="Segment end seconds"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return subtitle entries (start, duration, text) within a time range."""
    video = await _get_accessible_video(db, video_id, user)

    if not video.subtitle_json:
        return []

    entries = json.loads(video.subtitle_json)
    return [
        e for e in entries
        if e["start"] >= start - 0.5 and e["start"] < end
    ]


@router.get("/{video_id}/transcript", response_model=TranscriptOut)
async def get_transcript(
    video_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the full transcript as one ready-to-use string.

    The text is pre-joined (one line per subtitle, prefixed with `[MM:SS]`),
    so callers get the whole transcript without reassembling fragments. Use
    GET /{video_id}/subtitles instead when you need per-fragment timestamps.
    """
    video = await _get_accessible_video(db, video_id, user)

    transcript = video.raw_transcript or ""
    return TranscriptOut(
        video_id=video.id, title=video.title,
        transcript=transcript, char_count=len(transcript),
    )


def _parse_stored_subtitles(video: Video) -> list[SubtitleEntry] | None:
    """Parse subtitle_json into SubtitleEntry list; None when absent/unreadable."""
    if not video.subtitle_json:
        return None
    try:
        entries = json.loads(video.subtitle_json)
        if not isinstance(entries, list) or not entries:
            return None
        return [
            SubtitleEntry(
                start=e["start"], duration=e["duration"], text=e["text"],
                speaker=str(e.get("speaker", "") or ""),
            )
            for e in entries
        ]
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


@router.get("/{video_id}/dialogue", response_model=DialogueOut)
async def get_dialogue(
    video_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the dialogue-formatted transcript (speaker-labeled paragraphs).

    Paragraph text and turn start times are re-derived from the stored
    subtitle entries, so timestamps appear only at the start of each speaking
    turn. Returns 404 when dialogue has not been generated yet.
    """
    video = await _get_accessible_video(db, video_id, user)
    subtitles = _parse_stored_subtitles(video)
    if not video.dialogue_json or subtitles is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "dialogue not generated; POST to generate"
        )
    view = derive_dialogue_view(json.loads(video.dialogue_json), subtitles)
    return DialogueOut(video_id=video.id, **view)


@router.post("/{video_id}/dialogue", response_model=DialogueOut)
async def generate_dialogue(
    video_id: int,
    force: bool = Query(False, description="Regenerate even if dialogue already exists"),
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Generate (or with force=true, regenerate) the dialogue-formatted transcript.

    Runs LLM speaker/turn detection over the stored subtitles and saves the
    index-only layout to the video; the response is the rendered view.
    """
    result = await db.execute(
        select(Video).where(Video.id == video_id, Video.user_id == user.id)
    )
    video = result.scalar_one_or_none()
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")

    subtitles = _parse_stored_subtitles(video)
    if subtitles is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "No subtitles stored for this video"
        )

    if video.dialogue_json and not force:
        view = derive_dialogue_view(json.loads(video.dialogue_json), subtitles)
        return DialogueOut(video_id=video.id, **view)

    logger.info("[dialogue] generating for video_id=%d (force=%s, %d subtitles)",
                video.id, force, len(subtitles))
    stored, usage = await format_dialogue(
        subtitles,
        platform=video.platform, uploader=video.uploader,
        title=video.title, duration_seconds=video.duration_seconds,
    )
    video.dialogue_json = json.dumps(stored, ensure_ascii=False)
    if int(usage.get("llm_total_tokens") or 0):
        merge_llm_usage(video, usage)
        await accumulate_usage(db, video.user_id, usage)
    await db.commit()
    view = derive_dialogue_view(stored, subtitles)
    return DialogueOut(video_id=video.id, **view)
