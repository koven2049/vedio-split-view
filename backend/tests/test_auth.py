from __future__ import annotations

import pytest

from tests.conftest import admin_create_user, get_admin_token


@pytest.mark.asyncio
async def test_register_removed(client):
    """Registration endpoint should no longer exist."""
    resp = await client.post("/api/auth/register", json={"username": "testuser", "password": "testpass"})
    assert resp.status_code in (404, 405)


@pytest.mark.asyncio
async def test_admin_login(client):
    resp = await client.post("/api/auth/login", json={"username": "admin", "password": "test-admin-pass"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "admin"
    assert data["username"] == "admin"
    assert "lang_preference" in data


@pytest.mark.asyncio
async def test_user_login(client):
    token = await admin_create_user(client, "auth_user_login", role="user")
    resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    me = resp.json()
    assert me["username"] == "auth_user_login"
    assert me["role"] == "user"
    assert me["lang_preference"] == "zh"


@pytest.mark.asyncio
async def test_viewer_login(client):
    token = await admin_create_user(client, "auth_viewer_login", role="viewer")
    resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    me = resp.json()
    assert me["role"] == "viewer"
    assert me["lang_preference"] == "zh"


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    await admin_create_user(client, "wrongpw_user")
    resp = await client.post("/api/auth/login", json={"username": "wrongpw_user", "password": "wrong"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_unauthenticated_access(client):
    resp = await client.get("/api/videos")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_invalid_token(client):
    resp = await client.get("/api/auth/me", headers={"Authorization": "Bearer invalid-token"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_update_lang_preference(client):
    token = await admin_create_user(client, "lang_user", role="user")
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.put("/api/auth/lang", json={"lang": "en"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["lang_preference"] == "en"

    me_resp = await client.get("/api/auth/me", headers=headers)
    assert me_resp.json()["lang_preference"] == "en"

    resp2 = await client.put("/api/auth/lang", json={"lang": "zh"}, headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["lang_preference"] == "zh"


@pytest.mark.asyncio
async def test_update_lang_invalid(client):
    token = await admin_create_user(client, "lang_invalid_user", role="user")
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.put("/api/auth/lang", json={"lang": "fr"}, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_login_returns_lang(client):
    token = await admin_create_user(client, "lang_login_user", role="user")
    headers = {"Authorization": f"Bearer {token}"}
    await client.put("/api/auth/lang", json={"lang": "en"}, headers=headers)

    login_resp = await client.post("/api/auth/login", json={"username": "lang_login_user", "password": "pass123"})
    assert login_resp.status_code == 200
    assert login_resp.json()["lang_preference"] == "en"
