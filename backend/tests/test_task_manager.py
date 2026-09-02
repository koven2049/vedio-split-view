from __future__ import annotations

import pytest

from video_split.models import Task, User, Video
from video_split.service.auth_service import hash_password
from video_split.service.task_manager import (
    DuplicateVideoError,
    check_duplicate,
    check_task_quota,
    count_pending_tasks,
    create_task,
    discard_task,
    QuotaExceededError,
)


async def _create_test_user(db, username="task_test_user") -> User:
    user = User(username=username, password_hash=hash_password("pass"), role="admin")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest.mark.asyncio
async def test_create_task(db_session):
    user = await _create_test_user(db_session)
    task = await create_task(db_session, user.id, "https://youtube.com/watch?v=test", "youtube")
    assert task.id is not None
    assert task.user_id == user.id
    assert task.status == "downloading"
    assert task.platform == "youtube"


@pytest.mark.asyncio
async def test_count_pending_tasks(db_session):
    user = await _create_test_user(db_session, "count_user")

    count = await count_pending_tasks(db_session, user.id)
    assert count == 0

    task1 = Task(user_id=user.id, url="url1", platform="youtube", status="failed_transcribe")
    task2 = Task(user_id=user.id, url="url2", platform="youtube", status="failed_analyze")
    task3 = Task(user_id=user.id, url="url3", platform="youtube", status="completed")
    db_session.add_all([task1, task2, task3])
    await db_session.commit()

    count = await count_pending_tasks(db_session, user.id)
    assert count == 2  # only failed ones count


@pytest.mark.asyncio
async def test_task_quota_exceeded(db_session):
    user = await _create_test_user(db_session, "quota_user")

    for i in range(3):
        task = Task(user_id=user.id, url=f"url{i}", platform="youtube", status="failed_transcribe")
        db_session.add(task)
    await db_session.commit()

    with pytest.raises(QuotaExceededError):
        await check_task_quota(db_session, user.id)


@pytest.mark.asyncio
async def test_discard_task(db_session):
    user = await _create_test_user(db_session, "discard_user")
    task = Task(user_id=user.id, url="url", platform="youtube", status="failed_transcribe", temp_dir="")
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)

    task_id = task.id
    await discard_task(db_session, task)

    from sqlalchemy import select
    result = await db_session.execute(select(Task).where(Task.id == task_id))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_check_duplicate_matches_video_id_not_raw_url(db_session):
    user = await _create_test_user(db_session, "dup_user")
    video = Video(
        user_id=user.id,
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        platform="youtube",
        video_id="dQw4w9WgXcQ",
        title="已分析",
    )
    db_session.add(video)
    await db_session.commit()
    await db_session.refresh(video)

    with pytest.raises(DuplicateVideoError) as exc:
        await check_duplicate(db_session, user.id, "youtube", "dQw4w9WgXcQ")
    assert exc.value.existing_type == "video"
    assert exc.value.existing_id == video.id

    await db_session.delete(video)
    await db_session.commit()
    assert await check_duplicate(db_session, user.id, "youtube", "dQw4w9WgXcQ") is None
