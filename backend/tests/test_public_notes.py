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


async def test_public_note_includes_dialogue_paragraphs(client: AsyncClient, db_session):
    """Share page must get speaker paragraphs, not just line-per-timestamp text."""
    import json

    video = await _insert_note(db_session)
    video.subtitle_json = json.dumps(
        [
            {"start": 0.0, "duration": 2.0, "text": "你觉得新一代会是什么样子？"},
            {"start": 2.0, "duration": 3.0, "text": "我是说，从根本上说，"},
            {"start": 5.0, "duration": 3.0, "text": "是对寡头政治的关注。"},
        ],
        ensure_ascii=False,
    )
    video.dialogue_json = json.dumps(
        {
            "speakers": [
                {"id": "A", "name": "主持人", "role": ""},
                {"id": "B", "name": "嘉宾", "role": ""},
            ],
            "blocks": [{"s": "A", "f": 0, "t": 0}, {"s": "B", "f": 1, "t": 2}],
            "sections": [{"i": 0, "title": "开场"}],
            "fallback": False,
        },
        ensure_ascii=False,
    )
    await db_session.commit()

    sig = make_view_sig(video.id)
    body = (await client.get(f"/api/public/notes/{video.id}?sig={sig}")).json()
    dlg = body["dialogue"]
    assert dlg["single_speaker"] is False
    assert [b["speaker"] for b in dlg["blocks"]] == ["主持人", "嘉宾"]
    assert dlg["blocks"][1]["text"] == "我是说，从根本上说，是对寡头政治的关注。"
    assert dlg["blocks"][1]["start"] == 2.0
    assert dlg["sections"] == [{"index": 0, "title": "开场"}]
    # Raw transcript stays available as the fallback for old notes.
    assert body["transcript"] == "[00:00] hello"


async def test_public_note_without_dialogue_is_null(client: AsyncClient, db_session):
    video = await _insert_note(db_session)
    sig = make_view_sig(video.id)
    body = (await client.get(f"/api/public/notes/{video.id}?sig={sig}")).json()
    assert body["dialogue"] is None
