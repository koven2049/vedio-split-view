from __future__ import annotations

import pytest

from tests.conftest import admin_create_user, get_admin_token


@pytest.mark.asyncio
async def test_admin_list_users(client):
    admin_token = await get_admin_token(client)
    await admin_create_user(client, "admin_test_user1", role="user")
    await admin_create_user(client, "admin_test_viewer1", role="viewer")

    resp = await client.get("/api/admin/users", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    users = resp.json()
    usernames = [u["username"] for u in users]
    assert "admin_test_user1" in usernames
    assert "admin_test_viewer1" in usernames
    assert "admin" not in usernames

    viewer = next(u for u in users if u["username"] == "admin_test_viewer1")
    assert viewer["role"] == "viewer"


@pytest.mark.asyncio
async def test_admin_create_user_default_role(client):
    admin_token = await get_admin_token(client)
    resp = await client.post(
        "/api/admin/users",
        json={"username": "default_role_user", "password": "pass123"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "user"
    assert resp.json()["is_active"] is True


@pytest.mark.asyncio
async def test_admin_create_viewer(client):
    admin_token = await get_admin_token(client)
    resp = await client.post(
        "/api/admin/users",
        json={"username": "new_viewer", "password": "pass123", "role": "viewer"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "viewer"
    assert resp.json()["is_active"] is True

    login_resp = await client.post("/api/auth/login", json={"username": "new_viewer", "password": "pass123"})
    assert login_resp.status_code == 200
    assert login_resp.json()["role"] == "viewer"


@pytest.mark.asyncio
async def test_admin_create_invalid_role(client):
    admin_token = await get_admin_token(client)
    resp = await client.post(
        "/api/admin/users",
        json={"username": "invalid_role_user", "password": "pass123", "role": "superadmin"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_toggle_user(client):
    admin_token = await get_admin_token(client)
    await admin_create_user(client, "toggle_user", role="user")

    resp = await client.get("/api/admin/users", headers={"Authorization": f"Bearer {admin_token}"})
    target = next(u for u in resp.json() if u["username"] == "toggle_user")
    assert target["is_active"] is True

    resp2 = await client.put(f"/api/admin/users/{target['id']}/toggle", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp2.status_code == 200
    assert resp2.json()["is_active"] is False


@pytest.mark.asyncio
async def test_admin_delete_user(client):
    admin_token = await get_admin_token(client)
    await admin_create_user(client, "delete_admin_user", role="user")

    resp = await client.get("/api/admin/users", headers={"Authorization": f"Bearer {admin_token}"})
    target = next(u for u in resp.json() if u["username"] == "delete_admin_user")

    resp2 = await client.delete(f"/api/admin/users/{target['id']}", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp2.status_code == 204


@pytest.mark.asyncio
async def test_user_cannot_access_admin(client):
    user_token = await admin_create_user(client, "non_admin_user2", role="user")
    resp = await client.get("/api/admin/users", headers={"Authorization": f"Bearer {user_token}"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_viewer_cannot_access_admin(client):
    viewer_token = await admin_create_user(client, "viewer_admin_test", role="viewer")
    resp = await client.get("/api/admin/users", headers={"Authorization": f"Bearer {viewer_token}"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_analyze(client):
    admin_token = await get_admin_token(client)
    resp = await client.post(
        "/api/videos/analyze",
        json={"url": "https://www.youtube.com/watch?v=test123"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 403
