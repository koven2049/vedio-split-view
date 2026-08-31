from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from video_split.config import set_config_path
from video_split.service.downloader import (
    SubtitleEntry,
    VideoMeta,
    _fetch_bilibili_metadata_via_api,
    _fetch_bilibili_subtitles_via_api,
    extract_metadata,
    fetch_bilibili_subtitles,
)


@pytest.fixture(autouse=True)
def _app_config(test_config_path):
    set_config_path(test_config_path)


class MockResponse:
    """Mock httpx response."""

    def __init__(self, status_code: int, json_data: dict):
        self.status_code = status_code
        self._json = json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._json


def _mock_async_client(responses: list[MockResponse]) -> MagicMock:
    """Build mock httpx.AsyncClient that returns responses in order."""
    client = MagicMock()
    response_iter = iter(responses)

    async def _aget(url, **kwargs):
        return next(response_iter)

    client.get = MagicMock(side_effect=_aget)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


class TestFetchBilibiliMetadataViaApi:
    async def test_success(self):
        resp = MockResponse(200, {
            "code": 0,
            "data": {
                "bvid": "BV1xx411c7mD",
                "title": "Test Title",
                "duration": 123,
                "pic": "https://example.com/pic.jpg",
                "pubdate": 1609459200,
                "owner": {"name": "Test Uploader"},
            },
        })

        with patch("httpx.AsyncClient", return_value=_mock_async_client([resp])):
            meta = await _fetch_bilibili_metadata_via_api("BV1xx411c7mD", {})

        assert isinstance(meta, VideoMeta)
        assert meta.video_id == "BV1xx411c7mD"
        assert meta.title == "Test Title"
        assert meta.duration_seconds == 123
        assert meta.thumbnail_url == "https://example.com/pic.jpg"
        assert meta.upload_date == "2021-01-01"
        assert meta.uploader == "Test Uploader"
        assert meta.platform == "bilibili"

    async def test_api_error_code(self):
        resp = MockResponse(200, {"code": -404, "message": "Not Found"})

        with patch("httpx.AsyncClient", return_value=_mock_async_client([resp])):
            with pytest.raises(RuntimeError, match="Bilibili API error"):
                await _fetch_bilibili_metadata_via_api("BV1xx411c7mD", {})

    async def test_no_pubdate(self):
        resp = MockResponse(200, {
            "code": 0,
            "data": {
                "bvid": "BV1xx411c7mD",
                "title": "Test",
                "duration": 60,
                "pic": "",
                "pubdate": 0,
                "owner": {"name": ""},
            },
        })

        with patch("httpx.AsyncClient", return_value=_mock_async_client([resp])):
            meta = await _fetch_bilibili_metadata_via_api("BV1xx411c7mD", {})

        assert meta.upload_date == ""


class TestFetchBilibiliSubtitlesViaApi:
    async def test_success(self):
        view_resp = MockResponse(200, {
            "code": 0,
            "data": {"cid": 12345},
        })
        player_resp = MockResponse(200, {
            "code": 0,
            "data": {
                "subtitle": {
                    "subtitles": [
                        {
                            "lan": "zh-CN",
                            "lan_doc": "中文（中国）",
                            "subtitle_url": "//example.com/sub.json",
                        }
                    ]
                }
            },
        })
        sub_resp = MockResponse(200, {
            "body": [
                {"from": 0.0, "to": 2.5, "content": "Hello"},
                {"from": 2.5, "to": 5.0, "content": "World"},
            ]
        })

        with patch("httpx.AsyncClient", return_value=_mock_async_client([view_resp, player_resp, sub_resp])):
            entries = await _fetch_bilibili_subtitles_via_api("BV1xx411c7mD", {})

        assert len(entries) == 2
        assert entries[0] == SubtitleEntry(start=0.0, duration=2.5, text="Hello")
        assert entries[1] == SubtitleEntry(start=2.5, duration=2.5, text="World")

    async def test_no_subtitles(self):
        view_resp = MockResponse(200, {"code": 0, "data": {"cid": 12345}})
        player_resp = MockResponse(200, {
            "code": 0,
            "data": {"subtitle": {"subtitles": []}},
        })

        with patch("httpx.AsyncClient", return_value=_mock_async_client([view_resp, player_resp])):
            entries = await _fetch_bilibili_subtitles_via_api("BV1xx411c7mD", {})

        assert entries == []

    async def test_view_api_error(self):
        resp = MockResponse(200, {"code": -404, "message": "Not Found"})

        with patch("httpx.AsyncClient", return_value=_mock_async_client([resp])):
            entries = await _fetch_bilibili_subtitles_via_api("BV1xx411c7mD", {})

        assert entries == []

    async def test_player_api_error(self):
        view_resp = MockResponse(200, {"code": 0, "data": {"cid": 12345}})
        player_resp = MockResponse(200, {"code": -1, "message": "error"})

        with patch("httpx.AsyncClient", return_value=_mock_async_client([view_resp, player_resp])):
            entries = await _fetch_bilibili_subtitles_via_api("BV1xx411c7mD", {})

        assert entries == []

    async def test_lan_preference_zh_hans(self):
        """zh-Hans should be preferred over en."""
        view_resp = MockResponse(200, {"code": 0, "data": {"cid": 1}})
        player_resp = MockResponse(200, {
            "code": 0,
            "data": {
                "subtitle": {
                    "subtitles": [
                        {"lan": "en", "subtitle_url": "//e.com/en.json"},
                        {"lan": "zh-Hans", "subtitle_url": "//e.com/zh.json"},
                    ]
                }
            },
        })
        sub_resp = MockResponse(200, {"body": [{"from": 0, "to": 1, "content": "中文"}]})

        with patch("httpx.AsyncClient", return_value=_mock_async_client([view_resp, player_resp, sub_resp])):
            entries = await _fetch_bilibili_subtitles_via_api("BV1xx411c7mD", {})

        assert len(entries) == 1
        assert entries[0].text == "中文"

    async def test_https_protocol_added(self):
        """Subtitle URLs starting with // should get https: prefix."""
        view_resp = MockResponse(200, {"code": 0, "data": {"cid": 1}})
        player_resp = MockResponse(200, {
            "code": 0,
            "data": {
                "subtitle": {
                    "subtitles": [
                        {"lan": "zh-CN", "subtitle_url": "//example.com/sub.json"},
                    ]
                }
            },
        })
        sub_resp = MockResponse(200, {"body": [{"from": 0, "to": 1, "content": "hi"}]})

        mock_client = _mock_async_client([view_resp, player_resp, sub_resp])
        with patch("httpx.AsyncClient", return_value=mock_client):
            await _fetch_bilibili_subtitles_via_api("BV1xx411c7mD", {})

            calls = mock_client.get.call_args_list
            sub_call = calls[2]
            assert sub_call[0][0] == "https://example.com/sub.json"


_BILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}


class TestExtractMetadataUsesApiForBilibili:
    async def test_bilibili_uses_api(self):
        with (
            patch(
                "video_split.service.downloader._ensure_bilibili_fingerprint",
                new_callable=AsyncMock,
            ) as ensure,
            patch("video_split.service.downloader._fetch_bilibili_metadata_via_api") as mock_api,
        ):
            mock_api.return_value = VideoMeta(
                url="https://www.bilibili.com/video/BV1xx411c7mD",
                platform="bilibili",
                video_id="BV1xx411c7mD",
                title="API Title",
                duration_seconds=100,
                thumbnail_url="https://example.com/pic.jpg",
            )

            meta = await extract_metadata("https://www.bilibili.com/video/BV1xx411c7mD")

        ensure.assert_awaited_once()
        mock_api.assert_awaited_once_with("BV1xx411c7mD", _BILI_HEADERS)
        assert meta.title == "API Title"

    async def test_youtube_still_uses_ytdlp(self):
        with patch("yt_dlp.YoutubeDL") as mock_ydl:
            mock_instance = MagicMock()
            mock_instance.extract_info.return_value = {
                "id": "dQw4w9WgXcQ",
                "title": "YouTube Title",
                "duration": 200,
                "thumbnail": "https://yt.com/pic.jpg",
                "upload_date": "20210101",
                "uploader": "Uploader",
            }
            mock_ydl.return_value.__enter__.return_value = mock_instance

            meta = await extract_metadata("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

        assert meta.platform == "youtube"
        assert meta.title == "YouTube Title"


class TestFetchBilibiliSubtitlesUsesApi:
    async def test_uses_api(self):
        with (
            patch(
                "video_split.service.downloader._ensure_bilibili_fingerprint",
                new_callable=AsyncMock,
            ) as ensure,
            patch("video_split.service.downloader._fetch_bilibili_subtitles_via_api") as mock_api,
        ):
            mock_api.return_value = [SubtitleEntry(start=0, duration=1, text="Hello")]

            entries = await fetch_bilibili_subtitles("https://www.bilibili.com/video/BV1xx411c7mD")

        ensure.assert_awaited_once()
        mock_api.assert_awaited_once_with("BV1xx411c7mD", _BILI_HEADERS)
        assert len(entries) == 1
        assert entries[0].text == "Hello"
