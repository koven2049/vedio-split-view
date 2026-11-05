"""Apple Podcasts platform: URL detection, page parsing, playback links."""

from __future__ import annotations

import httpx
import pytest

from video_split.service import apple_podcasts as ap
from video_split.service.downloader import detect_platform, generate_playback_url

EP_URL = (
    "https://podcasts.apple.com/us/podcast/"
    "democrats-midterm-blowout-trump-blames-candidates-for/id1222114325?i=1000799790755"
)


# ─── URL detection ───


def test_detect_platform_apple_episode():
    platform, vid = detect_platform(EP_URL)
    assert platform == "apple_podcasts"
    assert vid == "1222114325:1000799790755"


def test_detect_platform_apple_other_locale_and_params():
    url = "https://podcasts.apple.com/jp/podcast/some-slug/id1200361736?uo=4&i=1000799791974"
    platform, vid = detect_platform(url)
    assert platform == "apple_podcasts"
    assert vid == "1200361736:1000799791974"


def test_detect_platform_apple_show_only_rejected():
    platform, vid = detect_platform(
        "https://podcasts.apple.com/us/podcast/up-first-from-npr/id1222114325"
    )
    assert (platform, vid) == ("unknown", "")


def test_playback_url_compound_video_id():
    url = generate_playback_url("apple_podcasts", "1222114325:1000799790755", 90)
    assert url == "https://podcasts.apple.com/us/podcast/id1222114325?i=1000799790755"


# ─── page parsing (fixture from the real page shape) ───

_PAGE = """
<html><head>
<meta property="og:title" content="Democrats' Midterm Blowout, Trump Blames Candidates For Losses"/>
<meta property="og:description" content="Podcast Episode · Up First from NPR · November 4 · 13m"/>
<meta property="og:image" content="https://is1-ssl.mzstatic.com/image/thumb/x.jpg"/>
</head><body>
<script type="application/ld+json">{"@type":"PodcastEpisode","name":"t","duration":"PT12M32S"}</script>
<script id="schema:AudioObject">{"@type":"AudioObject"}</script>
<script type="application/json">{"seasons":[],"episodes":[{"duration":752.373,
"mediaEnclosures":[{"streamUrl":"https:\\u002F\\u002Fcdn.example.com\\u002Fep\\u002Fdefault.mp3","mimeType":"audio/mpeg"}]}]}</script>
</body></html>
"""


class _FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, text):
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        return _FakeResponse(self._text)


@pytest.mark.asyncio
async def test_extract_metadata_parses_page(monkeypatch, test_config_path):
    from video_split.config import set_config_path

    set_config_path(test_config_path)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeClient(_PAGE))
    meta, audio_url = await ap.extract_apple_podcasts_metadata(EP_URL)
    assert meta.platform == "apple_podcasts"
    assert meta.video_id == "1222114325:1000799790755"
    assert meta.title.startswith("Democrats' Midterm Blowout")
    assert meta.uploader == "Up First from NPR"
    assert meta.duration_seconds == 752  # exact float beats LD minute-rounding
    assert audio_url == "https://cdn.example.com/ep/default.mp3"


def test_exact_duration_prefers_fractional_in_range():
    html = '{"a":{"duration":752.373},"b":{"duration":13}}'
    assert ap._exact_duration_seconds(html) == 752.373


def test_show_name_from_associated_podcast_fallback():
    html = (
        '<meta property="og:description" content="Podcast Episode"/>'
        '{"associatedPodcast":{"name":"The Daily"}}'
    )
    assert ap._show_name(html) == "The Daily"


@pytest.mark.asyncio
async def test_show_link_without_episode_param_rejected(monkeypatch, test_config_path):
    from video_split.config import set_config_path

    set_config_path(test_config_path)
    with pytest.raises(ap.ApplePodcastsError) as ei:
        await ap.extract_apple_podcasts_metadata(
            "https://podcasts.apple.com/us/podcast/up-first-from-npr/id1222114325"
        )
    assert ei.value.code == "not_episode"


@pytest.mark.asyncio
async def test_page_without_enclosure_raises_page_changed(monkeypatch, test_config_path):
    from video_split.config import set_config_path

    set_config_path(test_config_path)
    bare = '<meta property="og:title" content="T"/>'
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeClient(bare))
    with pytest.raises(ap.ApplePodcastsError) as ei:
        await ap.extract_apple_podcasts_metadata(EP_URL)
    assert ei.value.code == "page_changed"
