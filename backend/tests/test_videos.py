from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from video_split.models import Segment, User, Video
from tests.conftest import admin_create_user


async def _create_video(db: AsyncSession, user_id: int, title: str = "Test Video", is_public: bool = False) -> Video:
    video = Video(
        user_id=user_id, url="https://youtube.com/watch?v=abc", platform="youtube",
        video_id="abc", title=title, thumbnail_url="", duration_seconds=600,
        summary="A test video.", raw_transcript="...", is_public=is_public,
    )
    db.add(video)
    await db.flush()
    db.add(Segment(
        video_id=video.id, segment_index=0, title="Intro",
        summary="Introduction", start_seconds=0, end_seconds=300,
    ))
    db.add(Segment(
        video_id=video.id, segment_index=1, title="Main",
        summary="Main content", start_seconds=300, end_seconds=600,
    ))
    await db.commit()
    await db.refresh(video)
    return video


# ─── Basic user tests ───


@pytest.mark.asyncio
async def test_list_my_videos(client, db_session):
    token = await admin_create_user(client, "myvideos_user2")
    headers = {"Authorization": f"Bearer {token}"}

    result = await db_session.execute(select(User).where(User.username == "myvideos_user2"))
    user = result.scalar_one()
    await _create_video(db_session, user.id, "My Test Video 1")
    await _create_video(db_session, user.id, "My Test Video 2")

    resp = await client.get("/api/videos", headers=headers)
    assert resp.status_code == 200
    videos = resp.json()
    assert len(videos) == 2


@pytest.mark.asyncio
async def test_get_video_detail(client, db_session):
    token = await admin_create_user(client, "detail_user2")
    headers = {"Authorization": f"Bearer {token}"}

    result = await db_session.execute(select(User).where(User.username == "detail_user2"))
    user = result.scalar_one()
    video = await _create_video(db_session, user.id, "Detail Video 2")

    resp = await client.get(f"/api/videos/{video.id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Detail Video 2"
    assert len(data["segments"]) == 2


@pytest.mark.asyncio
async def test_delete_video(client, db_session):
    token = await admin_create_user(client, "delete_user2")
    headers = {"Authorization": f"Bearer {token}"}

    result = await db_session.execute(select(User).where(User.username == "delete_user2"))
    user = result.scalar_one()
    video = await _create_video(db_session, user.id, "To Delete 2")

    resp = await client.delete(f"/api/videos/{video.id}", headers=headers)
    assert resp.status_code == 204

    resp2 = await client.get(f"/api/videos/{video.id}", headers=headers)
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_share_unshare_video(client, db_session):
    token = await admin_create_user(client, "share_user2")
    headers = {"Authorization": f"Bearer {token}"}

    result = await db_session.execute(select(User).where(User.username == "share_user2"))
    user = result.scalar_one()
    video = await _create_video(db_session, user.id, "Shareable Video 2")

    resp = await client.post(f"/api/videos/{video.id}/share", headers=headers)
    assert resp.status_code == 200

    resp2 = await client.get("/api/videos/public", headers=headers)
    assert resp2.status_code == 200
    public_titles = [v["title"] for v in resp2.json()]
    assert "Shareable Video 2" in public_titles

    resp3 = await client.post(f"/api/videos/{video.id}/unshare", headers=headers)
    assert resp3.status_code == 200

    resp4 = await client.get("/api/videos/public", headers=headers)
    public_titles2 = [v["title"] for v in resp4.json()]
    assert "Shareable Video 2" not in public_titles2


@pytest.mark.asyncio
async def test_cannot_access_other_users_video(client, db_session):
    await admin_create_user(client, "owner_user2")
    token2 = await admin_create_user(client, "other_user2")

    result = await db_session.execute(select(User).where(User.username == "owner_user2"))
    user1 = result.scalar_one()
    video = await _create_video(db_session, user1.id, "Private Video 2")

    resp = await client.get(f"/api/videos/{video.id}", headers={"Authorization": f"Bearer {token2}"})
    assert resp.status_code == 403


# ─── Viewer role tests ───


@pytest.mark.asyncio
async def test_viewer_can_see_public_videos(client, db_session):
    user_token = await admin_create_user(client, "viewer_pub_owner")
    result = await db_session.execute(select(User).where(User.username == "viewer_pub_owner"))
    owner = result.scalar_one()
    video = await _create_video(db_session, owner.id, "Viewer Public Video", is_public=True)

    viewer_token = await admin_create_user(client, "viewer_pub_test", role="viewer")
    headers = {"Authorization": f"Bearer {viewer_token}"}

    resp = await client.get("/api/videos/public", headers=headers)
    assert resp.status_code == 200
    titles = [v["title"] for v in resp.json()]
    assert "Viewer Public Video" in titles


@pytest.mark.asyncio
async def test_viewer_can_view_public_detail(client, db_session):
    user_token = await admin_create_user(client, "viewer_detail_owner")
    result = await db_session.execute(select(User).where(User.username == "viewer_detail_owner"))
    owner = result.scalar_one()
    video = await _create_video(db_session, owner.id, "Viewer Detail Video", is_public=True)

    viewer_token = await admin_create_user(client, "viewer_detail_test", role="viewer")
    headers = {"Authorization": f"Bearer {viewer_token}"}

    resp = await client.get(f"/api/videos/{video.id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["title"] == "Viewer Detail Video"


@pytest.mark.asyncio
async def test_viewer_cannot_see_private_video(client, db_session):
    user_token = await admin_create_user(client, "viewer_priv_owner")
    result = await db_session.execute(select(User).where(User.username == "viewer_priv_owner"))
    owner = result.scalar_one()
    video = await _create_video(db_session, owner.id, "Private From Viewer", is_public=False)

    viewer_token = await admin_create_user(client, "viewer_priv_test", role="viewer")
    headers = {"Authorization": f"Bearer {viewer_token}"}

    resp = await client.get(f"/api/videos/{video.id}", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_list_my_videos(client):
    viewer_token = await admin_create_user(client, "viewer_no_my", role="viewer")
    headers = {"Authorization": f"Bearer {viewer_token}"}

    resp = await client.get("/api/videos", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_analyze(client):
    viewer_token = await admin_create_user(client, "viewer_no_analyze", role="viewer")
    headers = {"Authorization": f"Bearer {viewer_token}"}

    resp = await client.post(
        "/api/videos/analyze",
        json={"url": "https://www.youtube.com/watch?v=test123"},
        headers=headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_delete_video(client, db_session):
    user_token = await admin_create_user(client, "viewer_del_owner")
    result = await db_session.execute(select(User).where(User.username == "viewer_del_owner"))
    owner = result.scalar_one()
    video = await _create_video(db_session, owner.id, "Viewer No Delete", is_public=True)

    viewer_token = await admin_create_user(client, "viewer_no_delete", role="viewer")
    headers = {"Authorization": f"Bearer {viewer_token}"}

    resp = await client.delete(f"/api/videos/{video.id}", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_share_video(client, db_session):
    user_token = await admin_create_user(client, "viewer_share_owner")
    result = await db_session.execute(select(User).where(User.username == "viewer_share_owner"))
    owner = result.scalar_one()
    video = await _create_video(db_session, owner.id, "Viewer No Share", is_public=True)

    viewer_token = await admin_create_user(client, "viewer_no_share", role="viewer")
    headers = {"Authorization": f"Bearer {viewer_token}"}

    resp = await client.post(f"/api/videos/{video.id}/unshare", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_modify_tags(client, db_session):
    user_token = await admin_create_user(client, "viewer_tag_owner")
    result = await db_session.execute(select(User).where(User.username == "viewer_tag_owner"))
    owner = result.scalar_one()
    video = await _create_video(db_session, owner.id, "Viewer No Tag", is_public=True)

    viewer_token = await admin_create_user(client, "viewer_no_tag", role="viewer")
    headers = {"Authorization": f"Bearer {viewer_token}"}

    resp = await client.post(f"/api/tags/{video.id}/tags", json={"name": "NoPerms"}, headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_update_video(client, db_session):
    user_token = await admin_create_user(client, "viewer_upd_owner")
    result = await db_session.execute(select(User).where(User.username == "viewer_upd_owner"))
    owner = result.scalar_one()
    video = await _create_video(db_session, owner.id, "Viewer No Update", is_public=True)

    viewer_token = await admin_create_user(client, "viewer_no_update", role="viewer")
    headers = {"Authorization": f"Bearer {viewer_token}"}

    resp = await client.put(f"/api/videos/{video.id}", json={"title": "Hacked"}, headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_can_update_lang(client):
    viewer_token = await admin_create_user(client, "viewer_lang_test", role="viewer")
    headers = {"Authorization": f"Bearer {viewer_token}"}

    resp = await client.put("/api/auth/lang", json={"lang": "en"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["lang_preference"] == "en"
