"""GET /api/videos/{id}/transcript — full transcript as one string.

WHY: ai-learning needs the joined full text ready-to-use, without having to
fetch subtitle fragments and reassemble them. This endpoint exposes the
pre-joined raw_transcript directly.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from video_split.models import User
from tests.conftest import admin_create_user
from tests.test_videos import _create_video

_SAMPLE = "[00:00] hello there\n[00:05] welcome to the show"


@pytest.mark.asyncio
async def test_get_transcript_returns_full_text(client, db_session):
    token = await admin_create_user(client, "tr_full")
    result = await db_session.execute(select(User).where(User.username == "tr_full"))
    user = result.scalar_one()
    video = await _create_video(db_session, user.id, "Podcast Ep 1")
    video.raw_transcript = _SAMPLE
    await db_session.commit()

    resp = await client.get(
        f"/api/videos/{video.id}/transcript",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["video_id"] == video.id
    assert body["title"] == "Podcast Ep 1"
    assert body["transcript"] == _SAMPLE
    assert body["char_count"] == len(_SAMPLE)


@pytest.mark.asyncio
async def test_get_transcript_empty_when_no_subtitles(client, db_session):
    """A video with no transcript yields an empty string, not an error."""
    token = await admin_create_user(client, "tr_empty")
    result = await db_session.execute(select(User).where(User.username == "tr_empty"))
    user = result.scalar_one()
    video = await _create_video(db_session, user.id, "No Subs")
    video.raw_transcript = ""
    await db_session.commit()

    resp = await client.get(
        f"/api/videos/{video.id}/transcript",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["transcript"] == ""
    assert body["char_count"] == 0


@pytest.mark.asyncio
async def test_get_transcript_not_found(client):
    token = await admin_create_user(client, "tr_404")
    resp = await client.get(
        "/api/videos/999999/transcript",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_transcript_forbidden_for_other_users_private(client, db_session):
    await admin_create_user(client, "tr_owner")
    result = await db_session.execute(select(User).where(User.username == "tr_owner"))
    owner = result.scalar_one()
    video = await _create_video(db_session, owner.id, "Private", is_public=False)

    other_token = await admin_create_user(client, "tr_other")
    resp = await client.get(
        f"/api/videos/{video.id}/transcript",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_transcript_forbidden_for_viewer_on_private(client, db_session):
    """The viewer-role branch (distinct from non-owner) must also block private access."""
    await admin_create_user(client, "tr_priv_owner")
    result = await db_session.execute(select(User).where(User.username == "tr_priv_owner"))
    owner = result.scalar_one()
    video = await _create_video(db_session, owner.id, "Private", is_public=False)

    viewer_token = await admin_create_user(client, "tr_viewer", role="viewer")
    resp = await client.get(
        f"/api/videos/{video.id}/transcript",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_transcript_public_readable_by_non_owner(client, db_session):
    """A public video's transcript is readable by someone who doesn't own it."""
    await admin_create_user(client, "tr_pub_owner")
    result = await db_session.execute(select(User).where(User.username == "tr_pub_owner"))
    owner = result.scalar_one()
    video = await _create_video(db_session, owner.id, "Public Talk", is_public=True)

    other_token = await admin_create_user(client, "tr_pub_other")
    resp = await client.get(
        f"/api/videos/{video.id}/transcript",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["video_id"] == video.id
