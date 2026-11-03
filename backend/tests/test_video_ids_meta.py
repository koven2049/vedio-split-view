"""External read API: id list → metadata → detail, usable with an API token.

WHY: external scripts need to enumerate the library and fetch per-video
metadata without paging through full list rows or pulling heavy detail
payloads (segments, mindmap). These pin the three-tier contract and that it
works with X-API-Key (the documented external auth), not just a browser JWT.
"""
from __future__ import annotations

from tests.conftest import get_admin_token
from tests.test_videos import _admin_user, _create_video
from video_split.models import Tag

_META_FIELDS = {
    "id", "url", "platform", "video_id", "title", "thumbnail_url", "upload_date",
    "uploader", "duration_seconds", "is_public", "has_dialogue", "has_mindmap",
    "segment_count", "tags", "owner_name", "created_at", "updated_at",
}


async def _api_key_headers(client) -> dict[str, str]:
    jwt = await get_admin_token(client)
    resp = await client.post(
        "/api/settings/tokens", json={"name": "ext"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    return {"X-API-Key": resp.json()["full_key"]}


async def test_ids_lists_all_with_total(client, db_session):
    admin = await _admin_user(db_session)
    ids = [(await _create_video(db_session, admin.id, title=f"V{i}")).id for i in range(3)]
    headers = await _api_key_headers(client)

    resp = await client.get("/api/videos/ids", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert sorted(body["ids"]) == sorted(ids)


async def test_ids_filters_by_query_and_tag(client, db_session):
    admin = await _admin_user(db_session)
    a = await _create_video(db_session, admin.id, title="alpha talk")
    b = await _create_video(db_session, admin.id, title="beta talk")
    await _create_video(db_session, admin.id, title="gamma")
    tag = Tag(name="pod")
    db_session.add(tag)
    await db_session.flush()
    await db_session.refresh(b, ["tags"])
    b.tags.append(tag)
    await db_session.commit()
    headers = await _api_key_headers(client)

    q = (await client.get("/api/videos/ids", params={"q": "talk"}, headers=headers)).json()
    assert sorted(q["ids"]) == sorted([a.id, b.id]) and q["total"] == 2
    t = (await client.get("/api/videos/ids", params={"tag": "pod"}, headers=headers)).json()
    assert t == {"total": 1, "ids": [b.id]}


async def test_ids_requires_auth(client):
    assert (await client.get("/api/videos/ids")).status_code == 401


async def test_meta_is_light_and_complete(client, db_session):
    admin = await _admin_user(db_session)
    v = await _create_video(db_session, admin.id, title="Meta")
    v.mindmap_json = '{"chapters": []}'
    await db_session.commit()
    headers = await _api_key_headers(client)

    resp = await client.get(f"/api/videos/{v.id}/meta", headers=headers)
    assert resp.status_code == 200
    meta = resp.json()
    assert set(meta) == _META_FIELDS
    assert meta["title"] == "Meta"
    assert meta["segment_count"] == 2
    assert meta["has_mindmap"] is True and meta["has_dialogue"] is False
    # Light: no body text fields.
    assert not {"summary", "segments", "mindmap_json", "essence"} & set(meta)


async def test_meta_404(client):
    headers = await _api_key_headers(client)
    assert (await client.get("/api/videos/999999/meta", headers=headers)).status_code == 404


async def test_detail_works_with_api_key(client, db_session):
    admin = await _admin_user(db_session)
    v = await _create_video(db_session, admin.id)
    headers = await _api_key_headers(client)
    resp = await client.get(f"/api/videos/{v.id}", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()["segments"]) == 2


async def test_legacy_api_keys_alias_removed(client):
    jwt = await get_admin_token(client)
    resp = await client.get(
        "/api/settings/api-keys", headers={"Authorization": f"Bearer {jwt}"}
    )
    assert resp.status_code == 404
