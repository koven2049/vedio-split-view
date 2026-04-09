from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from video_split.database import get_db
from video_split.dependencies import require_user
from video_split.models import BilibiliCredential, Task, User
from video_split.schemas import TaskOut
from video_split.service.task_manager import (
    VideoLimitError,
    check_video_limit,
    discard_task,
    get_user_recoverable_tasks,
    get_user_tasks,
    RETRYABLE_STATUSES,
)
from video_split.service.task_runner import runner
from video_split.service.video_service import get_user_limits

router = APIRouter(prefix="/api/videos/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskOut])
async def list_tasks(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    tasks = await get_user_tasks(db, user.id)
    return tasks


@router.get("/recoverable", response_model=list[TaskOut])
async def list_recoverable_tasks(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    tasks = await get_user_recoverable_tasks(db, user.id)
    return tasks


@router.get("/active")
async def list_active_tasks(user: User = Depends(require_user)):
    """Return tasks currently running in the background for this user."""
    tasks = runner.active_for_user(user.id)
    return [
        {
            "task_id": rt.task_id,
            "platform": rt.platform,
            "url": rt.url,
            "finished": rt.finished,
            "last_progress": rt.last_progress,
        }
        for rt in tasks
    ]


@router.get("/{task_id}/stream")
async def stream_task_progress(
    task_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """SSE endpoint — replays past events then streams live updates."""
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == user.id)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")

    sub = runner.subscribe(task_id)
    if sub is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Task is not currently running (may have completed or been cleaned up)",
        )

    past_events, queue = sub

    async def _stream():
        try:
            for entry in past_events:
                yield entry
            while True:
                entry = await queue.get()
                if entry is None:
                    break
                yield entry
        finally:
            runner.unsubscribe(task_id, queue)

    return EventSourceResponse(_stream())


@router.post("/{task_id}/retry")
async def retry_task(
    task_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == user.id)
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    if task.status not in RETRYABLE_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Task status '{task.status}' is not retryable")

    try:
        await check_video_limit(db, user.id)
    except VideoLimitError as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e))

    cred_result = await db.execute(
        select(BilibiliCredential).where(BilibiliCredential.user_id == user.id)
    )
    bilibili_cred = cred_result.scalar_one_or_none()
    cred_kw: dict[str, str] = {}
    if bilibili_cred:
        cred_kw = {"sessdata": bilibili_cred.sessdata, "bili_jct": bilibili_cred.bili_jct, "buvid3": bilibili_cred.buvid3}

    limits = get_user_limits(user)

    from video_split.api.analysis import _start_background_analysis
    _start_background_analysis(
        task.id, user.id, task.platform, task.url,
        cred_kw=cred_kw, user_limits=limits, is_resume=True,
    )
    return {"task_id": task.id, "platform": task.platform, "resumed": True}


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == user.id)
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    runner.remove(task_id)
    await discard_task(db, task)
