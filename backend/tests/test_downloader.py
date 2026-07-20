from __future__ import annotations

import pytest

from video_split.config import get_settings
from video_split.service.downloader import _proxy_for_platform, detect_platform, generate_playback_url


class TestDetectPlatform:
    def test_youtube_standard(self):
        platform, vid = detect_platform("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert platform == "youtube"
        assert vid == "dQw4w9WgXcQ"

    def test_youtube_short(self):
        platform, vid = detect_platform("https://youtu.be/dQw4w9WgXcQ")
        assert platform == "youtube"
        assert vid == "dQw4w9WgXcQ"

    def test_youtube_embed(self):
        platform, vid = detect_platform("https://www.youtube.com/embed/dQw4w9WgXcQ")
        assert platform == "youtube"
        assert vid == "dQw4w9WgXcQ"

    def test_bilibili_standard(self):
        platform, vid = detect_platform("https://www.bilibili.com/video/BV1xx411c7mD")
        assert platform == "bilibili"
        assert vid == "BV1xx411c7mD"

    def test_bilibili_short(self):
        platform, vid = detect_platform("https://b23.tv/abc123")
        assert platform == "bilibili"
        assert vid == "abc123"

    def test_unknown(self):
        platform, vid = detect_platform("https://example.com/video")
        assert platform == "unknown"
        assert vid == ""

    def test_youtube_with_params(self):
        platform, vid = detect_platform("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=120")
        assert platform == "youtube"
        assert vid == "dQw4w9WgXcQ"


class TestGeneratePlaybackUrl:
    def test_youtube(self):
        url = generate_playback_url("youtube", "abc123", 120)
        assert url == "https://www.youtube.com/watch?v=abc123&t=120"

    def test_bilibili(self):
        url = generate_playback_url("bilibili", "BV1xx411c7mD", 60)
        assert url == "https://www.bilibili.com/video/BV1xx411c7mD?t=60"

    def test_unknown_platform(self):
        url = generate_playback_url("vimeo", "123", 30)
        assert url == ""


class TestProxyForPlatform:
    """Domestic platforms (bilibili, xiaoyuzhou) always bypass the global proxy;
    YouTube / unknown follow proxy_enabled."""

    def test_bilibili_always_direct_even_when_proxy_enabled(self, monkeypatch):
        s = get_settings()
        monkeypatch.setattr(s.network, "proxy_enabled", True)
        monkeypatch.setattr(s.network, "http_proxy", "http://127.0.0.1:7890")
        assert _proxy_for_platform("bilibili") is None

    def test_xiaoyuzhou_always_direct_even_when_proxy_enabled(self, monkeypatch):
        s = get_settings()
        monkeypatch.setattr(s.network, "proxy_enabled", True)
        monkeypatch.setattr(s.network, "http_proxy", "http://127.0.0.1:7890")
        assert _proxy_for_platform("xiaoyuzhou") is None

    def test_youtube_uses_proxy_when_enabled(self, monkeypatch):
        s = get_settings()
        monkeypatch.setattr(s.network, "proxy_enabled", True)
        monkeypatch.setattr(s.network, "http_proxy", "http://127.0.0.1:7890")
        assert _proxy_for_platform("youtube") == "http://127.0.0.1:7890"

    def test_youtube_direct_when_proxy_disabled(self, monkeypatch):
        s = get_settings()
        monkeypatch.setattr(s.network, "proxy_enabled", False)
        assert _proxy_for_platform("youtube") is None
