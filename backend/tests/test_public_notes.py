from __future__ import annotations

from httpx import AsyncClient

from sqlalchemy import select

from video_split.models import Segment, User, Video
from video_split.service.auth_service import ensure_admin_user
from video_split.share_token import make_view_sig


async def _insert_note(db_session) -> Video:
    await ensure_admin_user(db_session)
    admin = (await db_session.execute(select(User).where(User.username == "admin"))).scalar_one()
    video = Video(
        user_id=admin.id,
        url="https://youtu.be/abcdefghijk",
        platform="youtube",
        video_id="abcdefghijk",
        title="公开笔记",
        summary="整片摘要",
        essence="精华一段",
        raw_transcript="[00:00] hello",
        duration_seconds=90,
    )
    db_session.add(video)
    await db_session.flush()
    db_session.add(
        Segment(
            video_id=video.id,
            segment_index=0,
            title="第一段",
            summary="段摘要",
            start_seconds=0,
            end_seconds=90,
        )
    )
    await db_session.commit()
    await db_session.refresh(video)
    return video


async def test_public_note_valid_sig(client: AsyncClient, db_session):
    video = await _insert_note(db_session)
    sig = make_view_sig(video.id)
    resp = await client.get(f"/api/public/notes/{video.id}?sig={sig}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "公开笔记"
    assert body["summary"] == "整片摘要"
    assert body["essence"] == "精华一段"
    assert body["transcript"] == "[00:00] hello"
    assert body["segments"][0]["title"] == "第一段"
    assert "usage_json" not in body
    assert "owner_name" not in body


async def test_public_note_bad_sig_is_404(client: AsyncClient, db_session):
    video = await _insert_note(db_session)
    resp = await client.get(f"/api/public/notes/{video.id}?sig={'0' * 32}")
    assert resp.status_code == 404


async def test_public_note_missing_sig_is_422(client: AsyncClient):
    resp = await client.get("/api/public/notes/1")
    assert resp.status_code == 422


async def test_public_notes_hidden_from_apidocs(client: AsyncClient):
    data = (await client.get("/api/docs-data")).json()
    paths = [ep["path"] for g in data["groups"] for ep in g["endpoints"]]
    assert not any("/public/notes" in p for p in paths)
