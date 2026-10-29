"""Long-video confirmation: a timeout must say it timed out, not "cancelled"."""

from __future__ import annotations

import asyncio

import pytest

from video_split.config import set_config_path
from video_split.models import Task, User
from video_split.service import video_service
from video_split.service.downloader import VideoMeta


@pytest.mark.asyncio
async def test_confirm_timeout_reports_reason(db_session, test_config_path, monkeypatch):
    set_config_path(test_config_path)
    user = User(username="u1", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    task = Task(user_id=user.id, url="https://youtu.be/abc", platform="youtube")
    db_session.add(task)
    await db_session.commit()

    async def fake_meta(url, **_kw):
        return VideoMeta(url=url, platform="youtube", video_id="abc", title="t",
                         duration_seconds=1531, thumbnail_url="")

    async def fake_thumb(_meta):
        return ""

    monkeypatch.setattr(video_service, "extract_metadata", fake_meta)
    monkeypatch.setattr(video_service, "download_thumbnail", fake_thumb)
    monkeypatch.setattr(video_service, "CONFIRM_TIMEOUT_SECONDS", 0.01)

    events = [
        e async for e in video_service.run_analysis(
            db_session, task, asyncio.Event(), confirm_event=asyncio.Event(),
            user_limits={"confirm_threshold_seconds": 600},
        )
    ]

    assert [e.stage for e in events][-2:] == ["confirm_required", "cancelled"]
    assert events[-1].message == video_service.CONFIRM_TIMEOUT_MESSAGE
    assert task.status == "cancelled"
