from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from video_split.config import get_settings
from video_split.models import Task, Video

logger = logging.getLogger(__name__)

STUCK_TIMEOUT = timedelta(minutes=10)


RETRYABLE_STATUSES = {"failed_transcribe", "failed_analyze", "downloaded"}
ACTIVE_STATUSES = {"downloading", "transcribing", "analyzing"}
TERMINAL_STATUSES = {"completed", "cancelled", "failed_download"}
QUOTA_STATUSES = {"failed_transcribe", "failed_analyze", "downloaded"}


async def count_pending_tasks(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(Task)
        .where(Task.user_id == user_id, Task.status.in_(QUOTA_STATUSES))
    )
    return result.scalar() or 0


async def check_task_quota(db: AsyncSession, user_id: int, max_override: int = 0) -> None:
    settings = get_settings()
    limit = max_override or settings.storage.max_pending_tasks_per_user
    count = await count_pending_tasks(db, user_id)
    if count >= limit:
        raise QuotaExceededError(
            f"You have {count} unfinished tasks (max {limit}). "
            "Please retry or delete them before starting a new analysis."
        )


async def check_video_limit(db: AsyncSession, user_id: int) -> None:
    settings = get_settings()
    limit = settings.storage.max_total_videos_per_user
    result = await db.execute(
        select(func.count()).select_from(Video).where(Video.user_id == user_id)
    )
    count = result.scalar() or 0
    if count >= limit:
        raise VideoLimitError(
            f"You have reached the maximum of {limit} saved videos. "
            "Please delete some videos before analyzing new ones."
        )


async def create_task(db: AsyncSession, user_id: int, url: str, platform: str) -> Task:
    settings = get_settings()
    temp_base = Path(settings.storage.temp_dir)
    task = Task(
        user_id=user_id,
        url=url,
        platform=platform,
        status="downloading",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    task_dir = temp_base / str(task.id)
    task_dir.mkdir(parents=True, exist_ok=True)
    task.temp_dir = str(task_dir)
    await db.commit()
    return task


async def update_task_status(
    db: AsyncSession,
    task: Task,
    status: str,
    error_message: str = "",
    video_title: str | None = None,
) -> None:
    task.status = status
    if error_message:
        task.error_message = error_message
    if video_title is not None:
        task.video_title = video_title
    await db.commit()


async def cleanup_task_files(task: Task) -> None:
    if task.temp_dir:
        temp_path = Path(task.temp_dir)
        if temp_path.exists():
            shutil.rmtree(temp_path, ignore_errors=True)


async def discard_task(db: AsyncSession, task: Task) -> None:
    await cleanup_task_files(task)
    await db.delete(task)
    await db.commit()


async def get_user_tasks(db: AsyncSession, user_id: int) -> list[Task]:
    result = await db.execute(
        select(Task)
        .where(Task.user_id == user_id)
        .where(Task.status.notin_(TERMINAL_STATUSES))
        .order_by(Task.created_at.desc())
    )
    return list(result.scalars().all())


async def get_user_recoverable_tasks(db: AsyncSession, user_id: int) -> list[Task]:
    """Return tasks that should still be visible on Analyze page after restarts.

    This intentionally includes ``failed_download`` so interrupted jobs that were
    recovered during startup remain visible to the user instead of disappearing.
    """
    result = await db.execute(
        select(Task)
        .where(Task.user_id == user_id)
        .where(Task.status.notin_({"completed", "cancelled"}))
        .order_by(Task.created_at.desc())
    )
    return list(result.scalars().all())


async def get_task_by_id(db: AsyncSession, task_id: int, user_id: int) -> Task | None:
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == user_id)
    )
    return result.scalar_one_or_none()


def get_task_temp_dir(task: Task) -> Path:
    return Path(task.temp_dir)


class QuotaExceededError(Exception):
    pass


class VideoLimitError(Exception):
    pass


class DuplicateVideoError(Exception):
    """Raised when the user already has a task or analyzed video with the same video."""

    def __init__(self, message: str, existing_type: str, existing_id: int):
        super().__init__(message)
        self.existing_type = existing_type
        self.existing_id = existing_id


async def check_duplicate(
    db: AsyncSession, user_id: int, platform: str, video_id: str,
) -> Task | None:
    """Check for duplicates.

    Returns:
        A Task in retryable status if one exists (caller should resume it),
        or None if no duplicate found (caller should create a new task).

    Raises:
        DuplicateVideoError — if a completed video already exists or
            a task is genuinely in progress.
    """
    from video_split.service.downloader import detect_platform

    result = await db.execute(
        select(Video).where(
            Video.user_id == user_id,
            Video.platform == platform,
            Video.video_id == video_id,
        ).limit(1)
    )
    existing_video = result.scalar_one_or_none()
    if existing_video:
        raise DuplicateVideoError(
            f"Video already analyzed: 「{existing_video.title}」",
            existing_type="video",
            existing_id=existing_video.id,
        )

    tasks_result = await db.execute(
        select(Task).where(
            Task.user_id == user_id,
            Task.platform == platform,
            Task.status.notin_(TERMINAL_STATUSES),
        )
    )
    now = datetime.now(tz=timezone.utc)
    for task in tasks_result.scalars():
        _, tid = detect_platform(task.url)
        if tid != video_id:
            continue

        if task.status in RETRYABLE_STATUSES:
            logger.info("[dup-check] Found retryable task #%d (%s), will resume", task.id, task.status)
            return task

        if task.status in ACTIVE_STATUSES:
            created = task.created_at.replace(tzinfo=timezone.utc) if task.created_at.tzinfo is None else task.created_at
            age = now - created
            if age > STUCK_TIMEOUT:
                logger.warning("[dup-check] Task #%d stuck in '%s' for %s, auto-cleaning", task.id, task.status, age)
                await discard_task(db, task)
                continue
            raise DuplicateVideoError(
                f"Video is being processed ({task.status}): 「{task.video_title or task.url}」",
                existing_type="task",
                existing_id=task.id,
            )

    return None
