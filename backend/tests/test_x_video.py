"""X (Twitter) platform: URL detection/normalization, metadata, download options,
typed error codes.

X reuses the generic yt-dlp path, so the contract this file locks down is the
seam the generic path relies on: the tweet status id (never yt-dlp's media id)
is the video id, mirror domains normalize to x.com before yt-dlp sees them,
multi-video tweets collapse to the first entry, and yt-dlp failures surface as
a typed ``XPlatformError`` the task runner can persist.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import yt_dlp

from video_split.config import set_config_path
from video_split.service.downloader import (
    MIN_AUDIO_BYTES,
    XPlatformError,
    _proxy_for_platform,
    detect_platform,
    download_audio,
    extract_metadata,
    generate_playback_url,
    normalize_url,
)
from video_split.service.video_service import _PLATFORM_TAG_MAP

TWEET_URL = (
    "https://x.com/rchen1386/status/2124465845284008268"
    "?a=F25JJuNRRsuJxjooklI&s=20"
)
STATUS_ID = "2124465845284008268"
# yt-dlp reports the underlying media id in the tweet; it must never leak into
# VideoMeta.video_id (duplicate checks and resume keys key off the status id).
MEDIA_ID = "2124465754183725056"


@pytest.fixture(autouse=True)
def _app_config(test_config_path):
    set_config_path(test_config_path)


# ─── URL detection ───


class TestDetectPlatform:
    def test_canonical_url_with_tracking_query(self):
        assert detect_platform(TWEET_URL) == ("x", STATUS_ID)

    @pytest.mark.parametrize(
        "url",
        [
            f"https://twitter.com/someone/status/{STATUS_ID}",
            f"https://mobile.x.com/someone/status/{STATUS_ID}",
            f"https://m.twitter.com/someone/status/{STATUS_ID}",
            f"https://www.x.com/someone/status/{STATUS_ID}",
        ],
    )
    def test_host_variants(self, url):
        assert detect_platform(url) == ("x", STATUS_ID)

    @pytest.mark.parametrize(
        "url",
        [
            f"https://fxtwitter.com/someone/status/{STATUS_ID}",
            f"https://vxtwitter.com/someone/status/{STATUS_ID}",
            f"https://fixupx.com/someone/status/{STATUS_ID}",
            f"https://fixvx.com/someone/status/{STATUS_ID}",
        ],
    )
    def test_mirror_domains(self, url):
        assert detect_platform(url) == ("x", STATUS_ID)

    def test_i_web_status_path(self):
        assert detect_platform("https://x.com/i/web/status/1234567890") == (
            "x",
            "1234567890",
        )

    def test_i_status_path(self):
        assert detect_platform(f"https://x.com/i/status/{STATUS_ID}") == ("x", STATUS_ID)

    @pytest.mark.parametrize(
        "url",
        [
            "https://x.com/someone",  # profile, not a tweet
            "https://x.com/someone/status/not-a-number",
            "https://x.com/search?q=test",
            "https://example.com/someone/status/123",
        ],
    )
    def test_non_tweet_urls_rejected(self, url):
        assert detect_platform(url) == ("unknown", "")


# ─── URL normalization ───


class TestNormalizeUrl:
    @pytest.mark.parametrize(
        "url",
        [
            f"https://fxtwitter.com/someone/status/{STATUS_ID}",
            f"https://vxtwitter.com/someone/status/{STATUS_ID}",
            f"https://fixupx.com/someone/status/{STATUS_ID}",
            f"https://fixvx.com/someone/status/{STATUS_ID}",
            f"https://twitter.com/someone/status/{STATUS_ID}",
            f"https://mobile.x.com/someone/status/{STATUS_ID}",
        ],
    )
    def test_rewrites_to_canonical_x_url(self, url):
        assert normalize_url(url) == f"https://x.com/someone/status/{STATUS_ID}"

    def test_strips_query_and_fragment(self):
        assert normalize_url(TWEET_URL) == f"https://x.com/rchen1386/status/{STATUS_ID}"

    def test_i_web_status_canonicalized(self):
        assert (
            normalize_url("https://x.com/i/web/status/1234567890?ref=abc#x")
            == "https://x.com/i/status/1234567890"
        )

    def test_other_platforms_untouched(self):
        url = "https://www.bilibili.com/video/BV1xx411c7mD?p=2"
        assert normalize_url(url) == url


# ─── Playback link ───


def test_playback_url_has_no_timestamp():
    # X's web player cannot seek via URL, so only the tweet link is meaningful.
    assert (
        generate_playback_url("x", STATUS_ID, 120)
        == f"https://x.com/i/status/{STATUS_ID}"
    )


# ─── Proxy routing ───


class TestProxy:
    def test_x_uses_proxy_when_enabled(self, monkeypatch):
        from video_split.config import get_settings

        s = get_settings()
        monkeypatch.setattr(s.network, "proxy_enabled", True)
        monkeypatch.setattr(s.network, "http_proxy", "http://127.0.0.1:7890")
        assert _proxy_for_platform("x") == "http://127.0.0.1:7890"

    def test_x_direct_when_proxy_disabled(self, monkeypatch):
        from video_split.config import get_settings

        s = get_settings()
        monkeypatch.setattr(s.network, "proxy_enabled", False)
        assert _proxy_for_platform("x") is None


# ─── Metadata extraction ───


class _FakeYDL:
    """Record the URL/options yt-dlp receives and return ``info``."""

    info: dict | None = None
    seen_urls: list[str] = []
    seen_opts: list[dict] = []

    def __init__(self, opts):
        type(self).seen_opts.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        type(self).seen_urls.append(url)
        return type(self).info

    def download(self, urls):
        type(self).seen_urls.extend(urls)


_SINGLE_VIDEO = {
    "id": MEDIA_ID,
    "title": "Royce Chen - 卧槽！！OpenAI 联合创始人…",
    "description": (
        "卧槽！！OpenAI 联合创始人 Greg Brockman 在最新访谈里聊了很多\n"
        "https://t.co/abc123\n"
        "第二条正文"
    ),
    "duration": 336.9,
    "thumbnail": "https://pbs.twimg.com/media/abc.jpg",
    "upload_date": "20251101",
    "uploader": "Royce Chen",
}


@pytest.fixture
def fake_ydl(monkeypatch):
    _FakeYDL.info = None
    _FakeYDL.seen_urls = []
    _FakeYDL.seen_opts = []
    monkeypatch.setattr("yt_dlp.YoutubeDL", _FakeYDL)
    return _FakeYDL


class TestExtractMetadata:
    async def test_video_id_is_status_id_not_media_id(self, fake_ydl):
        fake_ydl.info = dict(_SINGLE_VIDEO)
        meta = await extract_metadata(TWEET_URL)
        assert meta.platform == "x"
        assert meta.video_id == STATUS_ID
        assert meta.video_id != MEDIA_ID
        assert meta.duration_seconds == 336

    async def test_title_comes_from_tweet_text(self, fake_ydl):
        fake_ydl.info = dict(_SINGLE_VIDEO)
        meta = await extract_metadata(TWEET_URL)
        assert "OpenAI" in meta.title
        assert "t.co" not in meta.title
        assert "\n" not in meta.title
        assert len(meta.title) <= 80

    async def test_title_falls_back_to_yt_dlp_title(self, fake_ydl):
        info = dict(_SINGLE_VIDEO, description="")
        fake_ydl.info = info
        meta = await extract_metadata(TWEET_URL)
        assert meta.title == info["title"]

    async def test_mirror_url_is_normalized_before_yt_dlp(self, fake_ydl):
        fake_ydl.info = dict(_SINGLE_VIDEO)
        await extract_metadata(f"https://vxtwitter.com/someone/status/{STATUS_ID}?s=20")
        assert fake_ydl.seen_urls == [f"https://x.com/someone/status/{STATUS_ID}"]

    async def test_multi_video_tweet_uses_first_entry(self, fake_ydl):
        fake_ydl.info = {
            "_type": "playlist",
            "id": STATUS_ID,
            "entries": [
                None,
                {
                    "id": MEDIA_ID,
                    "duration": 120.4,
                    "thumbnail": "https://pbs.twimg.com/media/first.jpg",
                    "upload_date": "20250102",
                },
                {"id": "other", "duration": 60},
            ],
        }
        meta = await extract_metadata(TWEET_URL)
        assert meta.duration_seconds == 120
        assert meta.video_id == STATUS_ID
        assert meta.thumbnail_url == "https://pbs.twimg.com/media/first.jpg"
        assert meta.upload_date == "2025-01-02"

    async def test_playlist_without_entries_does_not_crash(self, fake_ydl):
        fake_ydl.info = {"_type": "playlist", "id": STATUS_ID, "entries": []}
        meta = await extract_metadata(TWEET_URL)
        assert meta.duration_seconds == 0
        assert meta.video_id == STATUS_ID

    @pytest.mark.parametrize(
        ("message", "code"),
        [
            ("ERROR: No video could be found in this tweet", "x_no_video"),
            ("ERROR: [twitter] 123: NSFW tweet", "x_unavailable"),
            ("ERROR: [twitter] 123: This tweet is unavailable", "x_unavailable"),
            ("ERROR: [twitter] 123: Protected tweet", "x_unavailable"),
        ],
    )
    async def test_typed_error_codes(self, fake_ydl, message, code):
        def _raise(url, download=False):
            raise yt_dlp.utils.DownloadError(message)

        fake_ydl.info = None
        with (
            patch.object(_FakeYDL, "extract_info", side_effect=_raise),
            pytest.raises(XPlatformError) as exc,
        ):
            await extract_metadata(TWEET_URL)
        assert exc.value.code == code

    async def test_unrelated_yt_dlp_error_not_masked(self, fake_ydl):
        def _raise(url, download=False):
            raise yt_dlp.utils.DownloadError("ERROR: HTTP Error 500")

        with (
            patch.object(_FakeYDL, "extract_info", side_effect=_raise),
            pytest.raises(yt_dlp.utils.DownloadError),
        ):
            await extract_metadata(TWEET_URL)


# ─── Download options ───


class TestDownloadAudio:
    async def test_requests_first_playlist_item(self, fake_ydl, tmp_path):
        def _download(self, urls):
            _FakeYDL.seen_urls.extend(urls)
            (tmp_path / "audio.mp3").write_bytes(b"\0" * (MIN_AUDIO_BYTES + 1))

        fake_ydl.info = dict(_SINGLE_VIDEO)
        with patch.object(_FakeYDL, "download", _download):
            await download_audio(TWEET_URL, tmp_path)

        assert fake_ydl.seen_opts[0]["playlist_items"] == "1"
        assert fake_ydl.seen_urls == [f"https://x.com/rchen1386/status/{STATUS_ID}"]

    async def test_other_platforms_do_not_set_playlist_items(self, fake_ydl, tmp_path):
        def _download(self, urls):
            (tmp_path / "audio.mp3").write_bytes(b"\0" * (MIN_AUDIO_BYTES + 1))

        with patch.object(_FakeYDL, "download", _download):
            await download_audio("https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_path)

        assert "playlist_items" not in fake_ydl.seen_opts[0]


# ─── Platform tag ───


def test_platform_tag_registered():
    assert _PLATFORM_TAG_MAP["x"] == ("X", "#000000")
