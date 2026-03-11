from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from video_split.database import get_db
from video_split.dependencies import require_user
from video_split.models import BilibiliCredential, Task, User
from video_split.schemas import ProgressEvent, TaskOut
from video_split.service.task_manager import (
    VideoLimitError,
    check_video_limit,
    discard_task,
    get_user_tasks,
    RETRYABLE_STATUSES,
)
from video_split.service.video_service import resume_analysis

router = APIRouter(prefix="/api/videos/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskOut])
async def list_tasks(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    tasks = await get_user_tasks(db, user.id)
    return tasks


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

    cancel_event = asyncio.Event()
    cred_result = await db.execute(
        select(BilibiliCredential).where(BilibiliCredential.user_id == user.id)
    )
    bilibili_cred = cred_result.scalar_one_or_none()

    async def _stream():
        try:
            async for event in resume_analysis(db, task, cancel_event, bilibili_cred):
                yield {"event": event.stage, "data": event.model_dump_json()}
        except Exception as e:
            yield {"event": "error", "data": ProgressEvent(stage="error", progress=0, message=str(e)).model_dump_json()}

    return EventSourceResponse(_stream())


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
    await discard_task(db, task)
