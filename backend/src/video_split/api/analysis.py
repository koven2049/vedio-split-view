from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
)
from video_split.service.task_runner import runner
from video_split.service.video_service import get_user_limits

router = APIRouter(prefix="/api/videos", tags=["analysis"])


async def _get_bilibili_cred(db: AsyncSession, user_id: int) -> BilibiliCredential | None:
    result = await db.execute(
        select(BilibiliCredential).where(BilibiliCredential.user_id == user_id)
    )
    return result.scalar_one_or_none()


def _extract_cred_kwargs(cred: BilibiliCredential | None) -> dict[str, str]:
    if not cred:
        return {}
    return {"sessdata": cred.sessdata, "bili_jct": cred.bili_jct, "buvid3": cred.buvid3}


def _start_background_analysis(
    task_id: int,
    user_id: int,
    platform: str,
    url: str,
    *,
    cred_kw: dict[str, str],
    user_limits: dict[str, int],
    is_resume: bool = False,
) -> None:
    """Fire-and-forget: start analysis as a background asyncio task."""
    from video_split.service.video_service import run_analysis, resume_analysis
    from video_split.database import _get_session_factory

    def gen_factory(cancel_event, confirm_event):
        async def _gen():
            factory = _get_session_factory()
            async with factory() as db:
                result = await db.execute(select(Task).where(Task.id == task_id))
                task = result.scalar_one_or_none()
                if task is None:
                    yield ProgressEvent(stage="error", progress=0, message="Task not found")
                    return

                bilibili_cred = None
                if cred_kw:
                    cred_result = await db.execute(
                        select(BilibiliCredential).where(BilibiliCredential.user_id == user_id)
                    )
                    bilibili_cred = cred_result.scalar_one_or_none()

                if is_resume:
                    async for event in resume_analysis(db, task, cancel_event, bilibili_cred):
                        yield event
                else:
                    async for event in run_analysis(
                        db, task, cancel_event, bilibili_cred,
                        confirm_event=confirm_event, user_limits=user_limits,
                    ):
                        yield event
        return _gen()

    runner.start(task_id, user_id, platform, url, gen_factory)


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

    limits = get_user_limits(user)
    cred_kw = _extract_cred_kwargs(
        await _get_bilibili_cred(db, user.id) if platform == "bilibili" else None
    )

    try:
        retryable_task = await check_duplicate(db, user.id, platform, video_id)
    except DuplicateVideoError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))

    if retryable_task is not None:
        try:
            await check_video_limit(db, user.id)
        except VideoLimitError as e:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e))

        _start_background_analysis(
            retryable_task.id, user.id, platform, body.url,
            cred_kw=cred_kw, user_limits=limits, is_resume=True,
        )
        return {"task_id": retryable_task.id, "platform": platform, "resumed": True}

    try:
        await check_task_quota(db, user.id, max_override=limits["max_concurrent"], platform=platform)
    except QuotaExceededError as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e))

    try:
        await check_video_limit(db, user.id)
    except VideoLimitError as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e))

    task = await create_task(db, user.id, body.url, platform)

    _start_background_analysis(
        task.id, user.id, platform, body.url,
        cred_kw=cred_kw, user_limits=limits,
    )
    return {"task_id": task.id, "platform": platform}


@router.delete("/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    if runner.cancel(task_id):
        return {"message": "Cancellation requested"}

    result = await db.execute(
        select(Task).where(Task.id == task_id, Task.user_id == user.id)
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")

    from video_split.service.task_manager import update_task_status, cleanup_task_files
    await update_task_status(db, task, "cancelled")
    await cleanup_task_files(task)
    return {"message": "Task cancelled"}


@router.post("/tasks/{task_id}/confirm")
async def confirm_task(
    task_id: int,
    _user: User = Depends(require_user),
):
    if runner.confirm(task_id):
        return {"message": "Confirmed"}
    from video_split.api.debug import _debug_confirmations
    evt = _debug_confirmations.get(task_id)
    if evt:
        evt.set()
        return {"message": "Confirmed"}
    raise HTTPException(status.HTTP_404_NOT_FOUND, "No pending confirmation for this task")
