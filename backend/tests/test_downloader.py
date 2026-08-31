from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from video_split.config import get_settings, set_config_path
from video_split.service.downloader import (
    SubtitleEntry,
    _apply_youtube_opts,
    _parse_youtube_json3,
    _pick_bilibili_audio_url,
    _pick_youtube_subtitle_file,
    _pick_youtube_subtitle_url,
    _proxy_for_platform,
    detect_platform,
    fetch_youtube_subtitles,
    generate_playback_url,
)


@pytest.fixture(autouse=True)
def _app_config(test_config_path):
    set_config_path(test_config_path)


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


class TestApplyYoutubeOpts:
    def test_uses_deno_not_node(self, monkeypatch):
        monkeypatch.setattr(
            "video_split.service.downloader._youtube_cookies_path", lambda: None
        )
        opts: dict = {}
        _apply_youtube_opts(opts)
        assert opts["js_runtimes"] == {"deno": {}}
        assert "node" not in opts["js_runtimes"]
        assert "cookiefile" not in opts
        assert opts["extractor_args"]["youtube"]["player_client"] == [
            "web_embedded",
            "android",
        ]

    def test_attaches_cookiefile_when_present(self, monkeypatch):
        monkeypatch.setattr(
            "video_split.service.downloader._youtube_cookies_path",
            lambda: "/tmp/yt-cookies.txt",
        )
        opts: dict = {}
        _apply_youtube_opts(opts)
        assert opts["cookiefile"] == "/tmp/yt-cookies.txt"
        assert opts["js_runtimes"] == {"deno": {}}


class TestYoutubeSubtitleParse:
    def test_json3_skips_blank_events(self):
        data = {
            "events": [
                {"tStartMs": 0, "dDurationMs": 1500, "segs": [{"utf8": "Hello"}]},
                {"tStartMs": 1500, "dDurationMs": 500, "segs": [{"utf8": "\n"}]},
                {"tStartMs": 2000, "dDurationMs": 1000, "segs": [{"utf8": "World"}]},
            ]
        }
        entries = _parse_youtube_json3(data)
        assert entries == [
            SubtitleEntry(start=0.0, duration=1.5, text="Hello"),
            SubtitleEntry(start=2.0, duration=1.0, text="World"),
        ]

    def test_prefers_json3_url(self):
        tracks = [
            {"ext": "vtt", "url": "https://ex/a.vtt"},
            {"ext": "json3", "url": "https://ex/a.json3"},
        ]
        assert _pick_youtube_subtitle_url(tracks) == "https://ex/a.json3"

    def test_empty_tracks(self):
        assert _pick_youtube_subtitle_url([]) is None

    def test_picks_lang_ordered_json3_file(self, tmp_path):
        en = tmp_path / "vid.en.json3"
        zh = tmp_path / "vid.zh-Hans.json3"
        en.write_text("{}")
        zh.write_text("{}")
        assert _pick_youtube_subtitle_file([en, zh], "vid") == zh

    async def test_fetch_uses_ytdlp_json3(self):
        class FakeYDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def download(self, _urls):
                dest = Path(self.opts["outtmpl"]).parent / "rAk1wGn0hXs.en.json3"
                dest.write_text(
                    '{"events":[{"tStartMs":0,"dDurationMs":1000,"segs":[{"utf8":"字幕"}]}]}',
                    encoding="utf-8",
                )

        with (
            patch("yt_dlp.YoutubeDL", FakeYDL),
            patch(
                "video_split.service.downloader._fetch_youtube_subtitles_legacy",
                new_callable=AsyncMock,
            ) as legacy,
        ):
            entries = await fetch_youtube_subtitles("rAk1wGn0hXs")

        legacy.assert_not_called()
        assert entries == [SubtitleEntry(start=0.0, duration=1.0, text="字幕")]


class TestPickBilibiliAudioUrl:
    def test_picks_highest_bandwidth_dash(self):
        play = {
            "dash": {
                "audio": [
                    {"bandwidth": 1000, "baseUrl": "https://ex/low.m4s"},
                    {"bandwidth": 5000, "base_url": "https://ex/high.m4s"},
                ]
            }
        }
        assert _pick_bilibili_audio_url(play) == "https://ex/high.m4s"

    def test_falls_back_to_durl(self):
        play = {"durl": [{"url": "https://ex/durl.mp4"}]}
        assert _pick_bilibili_audio_url(play) == "https://ex/durl.mp4"
