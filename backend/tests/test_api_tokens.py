"""API token key-surface invariants (security red line, ../.workflow/security.md).

WHY: these were audited (hash at rest, one-time reveal, list echoes only the
identifier prefix) but never pinned by tests. The settings UI was reworked in
the same change, so lock the server-side contract it relies on.
"""
from __future__ import annotations

import hashlib

from sqlalchemy import select

from tests.conftest import admin_create_user, create_api_key, get_admin_token
from video_split.models import ApiKey


async def _jwt_headers(client) -> dict[str, str]:
    return {"Authorization": f"Bearer {await get_admin_token(client)}"}


async def test_full_key_returned_once_and_stored_hashed(client, db_session):
    headers = await _jwt_headers(client)
    created = await create_api_key(client, "bot")
    full = created["full_key"]
    assert full.startswith("vsk_") and len(full) > 40

    listed = (await client.get("/api/settings/tokens", headers=headers)).text
    assert full not in listed
    assert full[12:] not in listed  # nothing past the identifier prefix
    assert "key_hash" not in listed and "full_key" not in listed

    row = (await db_session.execute(select(ApiKey).where(ApiKey.id == created["id"]))).scalar_one()
    assert row.key_hash == hashlib.sha256(full.encode()).hexdigest()
    assert full not in (row.key_prefix, row.key_hash)


async def test_token_authenticates_until_deleted(client):
    headers = await _jwt_headers(client)
    created = await create_api_key(client, "bot")
    key_headers = {"X-API-Key": created["full_key"]}
    assert (await client.get("/api/videos/ids", headers=key_headers)).status_code == 200

    resp = await client.delete(f"/api/settings/tokens/{created['id']}", headers=headers)
    assert resp.status_code == 204
    assert (await client.get("/api/videos/ids", headers=key_headers)).status_code == 401


async def test_last_used_recorded(client):
    headers = await _jwt_headers(client)
    created = await create_api_key(client, "bot")
    assert created["last_used_at"] is None
    await client.get("/api/videos/ids", headers={"X-API-Key": created["full_key"]})
    listed = (await client.get("/api/settings/tokens", headers=headers)).json()
    assert listed[0]["last_used_at"] is not None


async def test_viewer_cannot_manage_tokens(client):
    viewer = {"Authorization": f"Bearer {await admin_create_user(client, 'tokviewer')}"}
    assert (await client.get("/api/settings/tokens", headers=viewer)).status_code == 403
    resp = await client.post("/api/settings/tokens", json={"name": "x"}, headers=viewer)
    assert resp.status_code == 403
