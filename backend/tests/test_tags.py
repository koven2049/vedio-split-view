from __future__ import annotations

import pytest

from tests.conftest import admin_create_user


@pytest.mark.asyncio
async def test_tag_crud(client):
    token = await admin_create_user(client, "tag_user_crud")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post("/api/tags", json={"name": "AI", "color": "#3b82f6"}, headers=headers)
    assert resp.status_code == 201
    tag = resp.json()
    assert tag["name"] == "AI"
    tag_id = tag["id"]

    resp2 = await client.get("/api/tags", headers=headers)
    assert resp2.status_code == 200
    tags = resp2.json()
    assert any(t["name"] == "AI" for t in tags)

    resp3 = await client.put(f"/api/tags/{tag_id}", json={"name": "Machine Learning"}, headers=headers)
    assert resp3.status_code == 200
    assert resp3.json()["name"] == "Machine Learning"

    resp4 = await client.delete(f"/api/tags/{tag_id}", headers=headers)
    assert resp4.status_code == 204


@pytest.mark.asyncio
async def test_duplicate_tag(client):
    token = await admin_create_user(client, "dup_tag_user2")
    headers = {"Authorization": f"Bearer {token}"}

    await client.post("/api/tags", json={"name": "Tutorial"}, headers=headers)
    resp = await client.post("/api/tags", json={"name": "Tutorial"}, headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_viewer_can_list_tags(client):
    user_token = await admin_create_user(client, "tag_list_owner", role="user")
    await client.post("/api/tags", json={"name": "ViewerVisible"}, headers={"Authorization": f"Bearer {user_token}"})

    viewer_token = await admin_create_user(client, "tag_viewer_list", role="viewer")
    resp = await client.get("/api/tags", headers={"Authorization": f"Bearer {viewer_token}"})
    assert resp.status_code == 200
    assert any(t["name"] == "ViewerVisible" for t in resp.json())


@pytest.mark.asyncio
async def test_viewer_cannot_create_tags(client):
    viewer_token = await admin_create_user(client, "tag_viewer_create", role="viewer")
    resp = await client.post("/api/tags", json={"name": "NoPerms"}, headers={"Authorization": f"Bearer {viewer_token}"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_delete_tags(client):
    viewer_token = await admin_create_user(client, "tag_viewer_delete", role="viewer")
    resp = await client.delete("/api/tags/999", headers={"Authorization": f"Bearer {viewer_token}"})
    assert resp.status_code == 403
