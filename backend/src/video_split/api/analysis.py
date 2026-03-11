from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from video_split.database import get_db
from video_split.dependencies import require_user
from video_split.models import BilibiliCredential, Task, User
from video_split.schemas import AnalyzeRequest, ProgressEvent
from video_split.service.downloader import detect_platform, normalize_url
from video_split.service.task_manager import (
    DuplicateVideoError,
    QuotaExceededError,
    VideoLimitError,
    check_duplicate,
    check_task_quota,
    check_video_limit,
    create_task,
    update_task_status,
)
from video_split.service.video_service import run_analysis, resume_analysis

router = APIRouter(prefix="/api/videos", tags=["analysis"])

_active_cancellations: dict[int, asyncio.Event] = {}
_active_confirmations: dict[int, asyncio.Event] = {}


async def _get_bilibili_cred(db: AsyncSession, user_id: int) -> BilibiliCredential | None:
    result = await db.execute(
        select(BilibiliCredential).where(BilibiliCredential.user_id == user_id)
    )
    return result.scalar_one_or_none()


def _make_sse_stream(db, task, cancel_event, bilibili_cred, *, is_resume: bool = False):
    """Build an SSE generator for analysis or resume."""
    confirm_event = asyncio.Event()
    _active_confirmations[task.id] = confirm_event

    async def _stream():
        try:
            if is_resume:
                gen = resume_analysis(db, task, cancel_event, bilibili_cred)
            else:
                gen = run_analysis(db, task, cancel_event, bilibili_cred, confirm_event=confirm_event)
            async for event in gen:
                yield {"event": event.stage, "data": event.model_dump_json()}
        except Exception as e:
            yield {"event": "error", "data": ProgressEvent(stage="error", progress=0, message=str(e)).model_dump_json()}
        finally:
            _active_cancellations.pop(task.id, None)
            _active_confirmations.pop(task.id, None)

    return _stream


@router.post("/analyze")
async def analyze_video(
    body: AnalyzeRequest,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    body.url = normalize_url(body.url)
    platform, video_id = detect_platform(body.url)
    if platform == "unknown":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported video URL")

    try:
        retryable_task = await check_duplicate(db, user.id, platform, video_id)
    except DuplicateVideoError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))

    if retryable_task is not None:
        try:
            await check_video_limit(db, user.id)
        except VideoLimitError as e:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e))

        cancel_event = asyncio.Event()
        _active_cancellations[retryable_task.id] = cancel_event
        bilibili_cred = await _get_bilibili_cred(db, user.id) if platform == "bilibili" else None
        return EventSourceResponse(
            _make_sse_stream(db, retryable_task, cancel_event, bilibili_cred, is_resume=True)()
        )

    try:
        await check_task_quota(db, user.id)
    except QuotaExceededError as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e))

    try:
        await check_video_limit(db, user.id)
    except VideoLimitError as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e))

    task = await create_task(db, user.id, body.url, platform)
    cancel_event = asyncio.Event()
    _active_cancellations[task.id] = cancel_event

    bilibili_cred = await _get_bilibili_cred(db, user.id) if platform == "bilibili" else None
    return EventSourceResponse(
        _make_sse_stream(db, task, cancel_event, bilibili_cred, is_resume=False)()
    )


@router.delete("/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    cancel_event = _active_cancellations.get(task_id)
    if cancel_event:
        cancel_event.set()
        return {"message": "Cancellation requested"}

    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == user.id)
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")

    from video_split.service.task_manager import cleanup_task_files
    await update_task_status(db, task, "cancelled")
    await cleanup_task_files(task)
    return {"message": "Task cancelled"}


@router.post("/tasks/{task_id}/confirm")
async def confirm_task(
    task_id: int,
    _user: User = Depends(require_user),
):
    confirm_event = _active_confirmations.get(task_id)
    if not confirm_event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No pending confirmation for this task")
    confirm_event.set()
    return {"message": "Confirmed"}
