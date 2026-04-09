"""Export / import video data as self-contained JSON files.

Each video is exported as `data/exports/<platform>_<video_id>.json`, containing
all metadata, segments, subtitles, and tag names. Thumbnails are already in
`data/thumbnails/` and synced alongside.

Import reads these JSON files and inserts any videos not already in the DB,
assigning them to a specified user.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from video_split.models import Segment, Tag, Video, video_tags

logger = logging.getLogger(__name__)

EXPORTS_DIR = Path("data/exports")


def _video_to_dict(video: Video, tags: list[str]) -> dict:
    return {
        "url": video.url,
        "platform": video.platform,
        "video_id": video.video_id,
        "status": video.status,
        "title": video.title,
        "thumbnail_url": video.thumbnail_url,
        "upload_date": video.upload_date,
        "duration_seconds": video.duration_seconds,
        "summary": video.summary,
        "summary_en": video.summary_en,
        "raw_transcript": video.raw_transcript,
        "subtitle_json": video.subtitle_json,
        "usage_json": video.usage_json,
        "is_public": video.is_public,
        "created_at": video.created_at.isoformat() if video.created_at else "",
        "tags": tags,
        "segments": [
            {
                "segment_index": seg.segment_index,
                "title": seg.title,
                "title_en": seg.title_en,
                "summary": seg.summary,
                "summary_en": seg.summary_en,
                "start_seconds": seg.start_seconds,
                "end_seconds": seg.end_seconds,
            }
            for seg in sorted(video.segments, key=lambda s: s.segment_index)
        ],
    }


def _export_filename(platform: str, vid: str) -> str:
    safe_vid = vid.replace("/", "_").replace("\\", "_")
    return f"{platform}_{safe_vid}.json"


async def export_video(video: Video, db: AsyncSession | None = None) -> Path:
    """Export a single video to a JSON file.

    If *db* is provided and relationships aren't loaded, the video is
    re-fetched with eager loading to avoid greenlet errors.
    If relationships are already populated (e.g. from ``selectinload``),
    *db* can be omitted.
    """
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    tags_loaded = "tags" in video.__dict__
    segs_loaded = "segments" in video.__dict__
    if db and (not tags_loaded or not segs_loaded):
        from sqlalchemy.orm import selectinload as _sel
        result = await db.execute(
            select(Video)
            .options(_sel(Video.tags), _sel(Video.segments))
            .where(Video.id == video.id)
        )
        video = result.scalar_one()

    tag_names = [t.name for t in video.tags]
    data = _video_to_dict(video, tag_names)
    path = EXPORTS_DIR / _export_filename(video.platform, video.video_id)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[export] Saved %s", path)
    return path


async def export_all_videos(db: AsyncSession, platform: str = "") -> int:
    """Export videos to JSON files. If *platform* is given, only that platform."""
    from sqlalchemy.orm import selectinload

    stmt = select(Video).options(selectinload(Video.segments), selectinload(Video.tags))
    if platform:
        stmt = stmt.where(Video.platform == platform)
    result = await db.execute(stmt)
    videos = result.scalars().unique().all()
    count = 0
    for v in videos:
        await export_video(v)
        count += 1
    logger.info("[export] Exported %d videos total", count)
    return count


async def import_videos(db: AsyncSession, target_user_id: int) -> dict:
    """Import videos from JSON files into DB, skipping duplicates.

    Returns {"imported": N, "skipped": N, "errors": [...]}.
    """
    if not EXPORTS_DIR.exists():
        return {"imported": 0, "skipped": 0, "errors": ["exports directory not found"]}

    imported = 0
    skipped = 0
    errors: list[str] = []

    for json_file in sorted(EXPORTS_DIR.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            errors.append(f"{json_file.name}: {e}")
            continue

        platform = data.get("platform", "")
        vid = data.get("video_id", "")
        if not platform or not vid:
            errors.append(f"{json_file.name}: missing platform or video_id")
            continue

        existing = await db.execute(
            select(Video).where(Video.platform == platform, Video.video_id == vid)
        )
        if existing.scalar_one_or_none() is not None:
            skipped += 1
            continue

        created_at = None
        if data.get("created_at"):
            try:
                created_at = datetime.fromisoformat(data["created_at"])
            except (ValueError, TypeError):
                pass

        video = Video(
            user_id=target_user_id,
            url=data.get("url", ""),
            platform=platform,
            video_id=vid,
            status=data.get("status", "done"),
            title=data.get("title", ""),
            thumbnail_url=data.get("thumbnail_url", ""),
            upload_date=data.get("upload_date", ""),
            duration_seconds=data.get("duration_seconds", 0),
            summary=data.get("summary", ""),
            summary_en=data.get("summary_en", ""),
            raw_transcript=data.get("raw_transcript", ""),
            subtitle_json=data.get("subtitle_json", ""),
            usage_json=data.get("usage_json", ""),
            is_public=data.get("is_public", False),
        )
        if created_at:
            video.created_at = created_at

        db.add(video)
        await db.flush()

        for seg_data in data.get("segments", []):
            db.add(Segment(
                video_id=video.id,
                segment_index=seg_data.get("segment_index", 0),
                title=seg_data.get("title", ""),
                title_en=seg_data.get("title_en", ""),
                summary=seg_data.get("summary", ""),
                summary_en=seg_data.get("summary_en", ""),
                start_seconds=seg_data.get("start_seconds", 0),
                end_seconds=seg_data.get("end_seconds", 0),
            ))

        for tag_name in data.get("tags", []):
            tag_result = await db.execute(select(Tag).where(Tag.name == tag_name))
            tag = tag_result.scalar_one_or_none()
            if tag is None:
                tag = Tag(name=tag_name)
                db.add(tag)
                await db.flush()
            await db.execute(video_tags.insert().values(video_id=video.id, tag_id=tag.id))

        imported += 1
        logger.info("[import] Imported %s (%s/%s)", json_file.name, platform, vid)

    await db.commit()
    logger.info("[import] Done: imported=%d skipped=%d errors=%d", imported, skipped, len(errors))
    return {"imported": imported, "skipped": skipped, "errors": errors}
