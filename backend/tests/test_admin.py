from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from tests.conftest import admin_create_user, get_admin_token
from video_split.config import get_settings
from video_split.models import Task, User, Video


@pytest.mark.asyncio
async def test_admin_list_users(client):
    admin_token = await get_admin_token(client)
    await admin_create_user(client, "admin_test_viewer1", role="viewer")

    resp = await client.get("/api/admin/users", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    users = resp.json()
    usernames = [u["username"] for u in users]
    assert "admin_test_viewer1" in usernames
    # admin is never listed (it is filtered out as the single privileged account)
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
    # Default (and only permitted) role is viewer.
    assert resp.json()["role"] == "viewer"
    assert resp.json()["is_active"] is True


@pytest.mark.asyncio
async def test_admin_cannot_create_admin(client):
    """admin is a single seeded account and cannot be created via the API."""
    admin_token = await get_admin_token(client)
    resp = await client.post(
        "/api/admin/users",
        json={"username": "another_admin", "password": "pass123", "role": "admin"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    # Rejected by the schema pattern (422) before reaching the 400 guard.
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_cannot_create_legacy_user_role(client):
    """The removed 'user' role is no longer creatable."""
    admin_token = await get_admin_token(client)
    resp = await client.post(
        "/api/admin/users",
        json={"username": "legacy_user", "password": "pass123", "role": "user"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422


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
    await admin_create_user(client, "toggle_user", role="viewer")

    resp = await client.get("/api/admin/users", headers={"Authorization": f"Bearer {admin_token}"})
    target = next(u for u in resp.json() if u["username"] == "toggle_user")
    assert target["is_active"] is True

    resp2 = await client.put(f"/api/admin/users/{target['id']}/toggle", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp2.status_code == 200
    assert resp2.json()["is_active"] is False


@pytest.mark.asyncio
async def test_admin_reset_user_password(client):
    admin_token = await get_admin_token(client)
    await admin_create_user(client, "reset_password_user", password="oldpass123", role="viewer")

    users_resp = await client.get("/api/admin/users", headers={"Authorization": f"Bearer {admin_token}"})
    target = next(u for u in users_resp.json() if u["username"] == "reset_password_user")

    reset_resp = await client.put(
        f"/api/admin/users/{target['id']}/password",
        json={"password": "newpass456"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert reset_resp.status_code == 200

    old_login = await client.post(
        "/api/auth/login",
        json={"username": "reset_password_user", "password": "oldpass123"},
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/api/auth/login",
        json={"username": "reset_password_user", "password": "newpass456"},
    )
    assert new_login.status_code == 200


@pytest.mark.asyncio
async def test_admin_delete_user(client):
    admin_token = await get_admin_token(client)
    await admin_create_user(client, "delete_admin_user", role="viewer")

    resp = await client.get("/api/admin/users", headers={"Authorization": f"Bearer {admin_token}"})
    target = next(u for u in resp.json() if u["username"] == "delete_admin_user")

    resp2 = await client.delete(f"/api/admin/users/{target['id']}", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp2.status_code == 204


@pytest.mark.asyncio
async def test_admin_delete_user_preview_and_cleanup_files(client, db_session, monkeypatch, tmp_path):
    from video_split.service import admin_cleanup as cleanup_service
    from video_split.service import data_sync

    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.setattr(data_sync, "EXPORTS_DIR", export_dir)
    monkeypatch.setattr(cleanup_service, "EXPORTS_DIR", export_dir)

    admin_token = await get_admin_token(client)
    await admin_create_user(client, "delete_with_files", role="viewer")

    users_resp = await client.get("/api/admin/users", headers={"Authorization": f"Bearer {admin_token}"})
    target = next(u for u in users_resp.json() if u["username"] == "delete_with_files")
    user_id = target["id"]

    settings = get_settings()
    thumb_dir = Path(settings.storage.temp_dir).parent / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    task_dir = Path(settings.storage.temp_dir) / "999"
    task_dir.mkdir(parents=True, exist_ok=True)

    export_path = export_dir / "youtube_abc123.json"
    thumb_path = thumb_dir / "youtube_abc123.jpg"
    export_path.write_text("{}", encoding="utf-8")
    thumb_path.write_text("thumb", encoding="utf-8")

    db_session.add(Video(
        user_id=user_id,
        url="https://www.youtube.com/watch?v=abc123",
        platform="youtube",
        video_id="abc123",
        title="Cleanup target",
        thumbnail_url="/api/thumbnails/youtube_abc123.jpg",
        duration_seconds=120,
        is_public=True,
    ))
    db_session.add(Task(
        user_id=user_id,
        url="https://www.youtube.com/watch?v=abc123",
        platform="youtube",
        status="failed_transcribe",
        temp_dir=str(task_dir),
    ))
    await db_session.commit()

    preview_resp = await client.get(
        f"/api/admin/users/{user_id}/delete-preview",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert preview_resp.status_code == 200
    preview = preview_resp.json()
    assert preview["library_videos"] == 1
    assert preview["public_videos"] == 1
    assert preview["private_videos"] == 0
    assert preview["export_files"] == 1
    assert preview["thumbnail_files"] == 1
    assert preview["temp_dirs"] == 1

    delete_resp = await client.delete(
        f"/api/admin/users/{user_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert delete_resp.status_code == 204
    assert not export_path.exists()
    assert not thumb_path.exists()
    assert not task_dir.exists()

    remaining_user = await db_session.execute(select(User).where(User.id == user_id))
    assert remaining_user.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_admin_cleanup_summary_and_run(client, monkeypatch, tmp_path):
    from video_split.service import admin_cleanup as cleanup_service
    from video_split.service import data_sync

    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    monkeypatch.setattr(data_sync, "EXPORTS_DIR", export_dir)
    monkeypatch.setattr(cleanup_service, "EXPORTS_DIR", export_dir)

    settings = get_settings()
    thumb_dir = Path(settings.storage.temp_dir).parent / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    task_dir = Path(settings.storage.temp_dir) / "456"
    task_dir.mkdir(parents=True, exist_ok=True)

    orphan_export = export_dir / "youtube_orphan.json"
    orphan_thumb = thumb_dir / "orphan.jpg"
    orphan_export.write_text("{}", encoding="utf-8")
    orphan_thumb.write_text("thumb", encoding="utf-8")

    admin_token = await get_admin_token(client)
    summary_resp = await client.get(
        "/api/admin/cleanup/summary",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert summary["orphan_exports"] == 1
    assert summary["orphan_thumbnails"] == 1
    assert summary["orphan_task_dirs"] == 1

    cleanup_resp = await client.post(
        "/api/admin/cleanup/run",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert cleanup_resp.status_code == 200
    body = cleanup_resp.json()
    assert body["removed_exports"] == 1
    assert body["removed_thumbnails"] == 1
    assert body["removed_task_dirs"] == 1
    assert body["errors"] == []
    assert not orphan_export.exists()
    assert not orphan_thumb.exists()
    assert not task_dir.exists()


@pytest.mark.asyncio
async def test_viewer_cannot_access_admin(client):
    viewer_token = await admin_create_user(client, "viewer_admin_test", role="viewer")
    resp = await client.get("/api/admin/users", headers={"Authorization": f"Bearer {viewer_token}"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_analyze(client, monkeypatch):
    """Admin now has analysis capability (the removed 'user' role's job)."""
    from video_split.api import analysis as analysis_api

    started: dict[str, int] = {}

    def _fake_start(task_id, user_id, platform, url, **kwargs):
        started["task_id"] = task_id

    monkeypatch.setattr(analysis_api, "_start_background_analysis", _fake_start)

    admin_token = await get_admin_token(client)
    resp = await client.post(
        "/api/videos/analyze",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["platform"] == "youtube"
    assert "task_id" in started
