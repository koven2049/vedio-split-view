from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import admin_create_user, get_admin_token


@pytest.mark.asyncio
async def test_youtube_cookies_status_reports_probe_failure(client, test_config_path, monkeypatch):
    from video_split.api import youtube as youtube_api

    config_dir = Path(test_config_path).parent
    cookie_file = config_dir / "test-youtube-cookies.txt"
    cookie_file.write_text(
        "\n".join([
            "# Netscape HTTP Cookie File",
            ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\ttest-sid",
            ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tLOGIN_INFO\ttest-login",
        ]),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        youtube_api,
        "_probe_youtube_cookiefile",
        lambda: (False, "Configured cookies are present, but YouTube still requires bot/login verification."),
    )

    token = await get_admin_token(client)
    resp = await client.get(
        "/api/youtube/cookies-status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["file_exists"] is True
    assert body["expired"] is False
    assert body["usability_checked"] is True
    assert body["usable"] is False
    assert "bot/login verification" in body["usability_message"]


@pytest.mark.asyncio
async def test_youtube_cookies_status_allows_admin(client, test_config_path, monkeypatch):
    from video_split.api import youtube as youtube_api

    config_dir = Path(test_config_path).parent
    cookie_file = config_dir / "test-youtube-cookies.txt"
    cookie_file.write_text(
        "\n".join([
            "# Netscape HTTP Cookie File",
            ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\ttest-sid",
            ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tLOGIN_INFO\ttest-login",
        ]),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        youtube_api,
        "_probe_youtube_cookiefile",
        lambda: (True, "Cookies look usable for yt-dlp metadata requests."),
    )

    token = await get_admin_token(client)
    resp = await client.get(
        "/api/youtube/cookies-status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["usable"] is True


@pytest.mark.asyncio
async def test_youtube_cookies_status_allows_viewer(client):
    """cookies-status is a read endpoint; viewers (read-only) may access it."""
    token = await admin_create_user(client, "youtube_probe_viewer", role="viewer")
    resp = await client.get(
        "/api/youtube/cookies-status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
