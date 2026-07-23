"""Tests for XiaoyuzhouError typing (cdn_expired / paid_private / page_changed / not_episode).

These tests do not hit the network: httpx.AsyncClient is monkeypatched to
return fixture HTML so classification is purely driven by page content.
"""
from __future__ import annotations

import json

import pytest

from video_split.service import xiaoyuzhou as xy
from video_split.service.task_runner import TaskRunner
from video_split.schemas import ProgressEvent


VALID_EPISODE_URL = "https://www.xiaoyuzhoufm.com/episode/67d32a01f4f4f4f4f4f4f4f4"


def _build_html(*, title: str = "", audio: str = "", description: str = "",
                json_ld: dict | None = None, body_text: str = "") -> str:
    """Construct a synthetic 小宇宙 episode page for testing."""
    parts = ["<html><head>"]
    if title:
        parts.append(f'<meta property="og:title" content="{title}"/>')
    if audio:
        parts.append(f'<meta property="og:audio" content="{audio}"/>')
    parts.append('<meta property="og:image" content="https://img.example.com/x.png"/>')
    if description:
        parts.append(f'<meta property="og:description" content="{description}"/>')
    if json_ld is not None:
        parts.append(
            '<script type="application/ld+json">'
            + json.dumps(json_ld)
            + '</script>'
        )
    parts.append("</head><body>")
    parts.append(body_text or "")
    parts.append("</body></html>")
    return "".join(parts)


def _podcast_episode_ld(*, name: str = "Ep", duration: str = "PT30M",
                        content_url: str = "", date_published: str = "2026-03-19") -> dict:
    obj: dict = {
        "@type": "PodcastEpisode",
        "name": name,
        "timeRequired": duration,
        "datePublished": date_published,
        "partOfSeries": {"@type": "PodcastSeries", "name": "Test Show"},
    }
    if content_url:
        obj["contentUrl"] = content_url
    return obj


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                "fake status", request=None, response=self,  # type: ignore[arg-type]
            )


def _patch_async_client(monkeypatch, *, html: str, status_code: int = 200):
    """Replace httpx.AsyncClient in xiaoyuzhou module with one returning `html`."""
    response = _FakeResponse(html, status_code=status_code)

    class _FakeStream:
        def __init__(self, resp):
            self._resp = resp

        async def __aenter__(self):
            return self._resp

        async def __aexit__(self, *exc):
            return False

        async def aiter_bytes(self):
            yield b"fake-audio-bytes"

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, url):
            return _Requestable(response)

        def stream(self, method, url):
            return _FakeStream(_FakeResponse(html, status_code=status_code))

    class _Requestable:
        """Tiny awaitable wrapper that returns `response` when awaited."""

        def __init__(self, resp):
            self._resp = resp

        def __await__(self):
            async def _coro():
                return self._resp
            return _coro().__await__()

    monkeypatch.setattr(xy.httpx, "AsyncClient", _FakeClient)


# ---------------------------------------------------------------------------
# extract_xiaoyuzhou_metadata classification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cdn_expired_no_og_audio(monkeypatch):
    """Page is a valid episode (has title + PodcastEpisode JSON-LD) but no audio link."""
    html = _build_html(
        title="公开单集标题",
        json_ld=_podcast_episode_ld(duration="PT40M", content_url=""),
    )
    _patch_async_client(monkeypatch, html=html)

    with pytest.raises(xy.XiaoyuzhouError) as ei:
        await xy.extract_xiaoyuzhou_metadata(VALID_EPISODE_URL)
    assert ei.value.code == "cdn_expired"


@pytest.mark.asyncio
async def test_paid_private(monkeypatch):
    """Page contains paywall/private markers and no audio → paid_private."""
    html = _build_html(
        title="付费单集标题",
        json_ld=_podcast_episode_ld(duration="PT0M", content_url=""),
        body_text='<div class="paywall">登录后可收听完整内容，VIP 会员免费</div>',
    )
    _patch_async_client(monkeypatch, html=html)

    with pytest.raises(xy.XiaoyuzhouError) as ei:
        await xy.extract_xiaoyuzhou_metadata(VALID_EPISODE_URL)
    assert ei.value.code == "paid_private"


@pytest.mark.asyncio
async def test_paid_private_not_triggered_when_audio_present(monkeypatch):
    """Shownotes mentioning 付费/会员 must NOT misclassify a public episode that
    has a usable audio URL — should fall through to success (or cdn_expired),
    not paid_private. Guards against false positives from topical body text."""
    html = _build_html(
        title="公开单集：讨论付费内容产业",
        audio="https://cdn.xiaoyuzhoufm.com/audio.m4a",
        json_ld=_podcast_episode_ld(duration="PT45M", content_url="https://cdn.xiaoyuzhoufm.com/audio.m4a"),
        body_text="<p>本期聊聊付费墙、VIP 会员经济</p>",
    )
    _patch_async_client(monkeypatch, html=html)

    meta, audio_url = await xy.extract_xiaoyuzhou_metadata(VALID_EPISODE_URL)
    assert audio_url  # succeeded — not classified as paid_private
    assert meta.title == "公开单集：讨论付费内容产业"


@pytest.mark.asyncio
async def test_page_changed(monkeypatch):
    """Neither og:title nor PodcastEpisode JSON-LD present → page_changed."""
    html = _build_html(title="", body_text="<div>404</div>")
    _patch_async_client(monkeypatch, html=html)

    with pytest.raises(xy.XiaoyuzhouError) as ei:
        await xy.extract_xiaoyuzhou_metadata(VALID_EPISODE_URL)
    assert ei.value.code == "page_changed"


@pytest.mark.asyncio
async def test_not_episode(monkeypatch):
    """URL is not an /episode/ link → not_episode (XiaoyuzhouError, not ValueError)."""
    # No HTTP call should happen; even if it does, the body is irrelevant.
    _patch_async_client(monkeypatch, html=_build_html(title="x"))

    with pytest.raises(xy.XiaoyuzhouError) as ei:
        await xy.extract_xiaoyuzhou_metadata("https://www.xiaoyuzhoufm.com/podcast/abc")
    assert ei.value.code == "not_episode"


@pytest.mark.asyncio
async def test_valid_metadata_returns_meta(monkeypatch):
    """Sanity check: a well-formed page still parses correctly."""
    html = _build_html(
        title="正常单集",
        audio="https://cdn.example.com/xiaoyuzhou/audio.m4a",
        json_ld=_podcast_episode_ld(duration="PT1H", content_url="https://cdn.example.com/x.m4a"),
    )
    _patch_async_client(monkeypatch, html=html)

    meta, audio_url = await xy.extract_xiaoyuzhou_metadata(VALID_EPISODE_URL)
    assert audio_url == "https://cdn.example.com/xiaoyuzhou/audio.m4a"
    assert meta.title == "正常单集"
    assert meta.platform == "xiaoyuzhou"


# ---------------------------------------------------------------------------
# download_xiaoyuzhou_audio classification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_download_cdn_expired_on_http_403(monkeypatch, tmp_path):
    """403 from CDN → XiaoyuzhouError(cdn_expired)."""

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url):
            import httpx
            req = httpx.Request("GET", url)

            class _Resp:
                status_code = 403
                headers = {}

                def raise_for_status(self):
                    raise httpx.HTTPStatusError(
                        "forbidden", request=req, response=httpx.Response(403, request=req),
                    )

            class _StreamCtx:
                async def __aenter__(self):
                    return _Resp()

                async def __aexit__(self, *exc):
                    return False

                async def aiter_bytes(self):
                    yield b""
                    return

            return _StreamCtx()

    monkeypatch.setattr(xy.httpx, "AsyncClient", _FakeClient)

    with pytest.raises(xy.XiaoyuzhouError) as ei:
        await xy.download_xiaoyuzhou_audio(
            "https://cdn.example.com/expired.m4a", tmp_path,
        )
    assert ei.value.code == "cdn_expired"


# ---------------------------------------------------------------------------
# task_runner integration: error_code propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_runner_propagates_error_code(monkeypatch):
    """XiaoyuzhouError surfacing in run_analysis produces an error event with error_code."""

    async def _gen_factory(cancel_event, confirm_event):
        yield ProgressEvent(stage="metadata", progress=5, message="...")
        raise xy.XiaoyuzhouError("cdn_expired", "fake CDN expiry")

    runner = TaskRunner()
    rt = runner.start(
        task_id=99001, user_id=1, platform="xiaoyuzhou",
        url=VALID_EPISODE_URL, gen_factory=_gen_factory,
    )

    # Wait until background task finishes (event injected into rt.events).
    import asyncio as _asyncio
    for _ in range(100):
        if rt.finished:
            break
        await _asyncio.sleep(0.01)

    error_events = [e for e in rt.events if e["event"] == "error"]
    assert error_events, "expected an error event"
    payload = json.loads(error_events[-1]["data"])
    assert payload["stage"] == "error"
    assert payload["detail"] is not None
    assert payload["detail"].get("error_code") == "cdn_expired"


@pytest.mark.asyncio
async def test_task_runner_no_error_code_for_generic_exception():
    """Generic Exception should NOT carry error_code in detail."""

    async def _gen_factory(cancel_event, confirm_event):
        yield ProgressEvent(stage="metadata", progress=5, message="...")
        raise RuntimeError("totally unrelated boom")

    runner = TaskRunner()
    rt = runner.start(
        task_id=99002, user_id=1, platform="xiaoyuzhou",
        url=VALID_EPISODE_URL, gen_factory=_gen_factory,
    )

    import asyncio as _asyncio
    for _ in range(100):
        if rt.finished:
            break
        await _asyncio.sleep(0.01)

    error_events = [e for e in rt.events if e["event"] == "error"]
    assert error_events
    payload = json.loads(error_events[-1]["data"])
    # Either detail is None, or detail has no error_code key.
    if payload["detail"] is not None:
        assert "error_code" not in payload["detail"]


@pytest.mark.asyncio
async def test_task_runner_duration_exceeded_error_code():
    """Exceeding max_duration is deterministic — the error event must carry
    error_code=duration_exceeded so the frontend can hide the (useless) retry
    button instead of inviting the user to retry an impossible task."""
    from video_split.service.video_service import DurationLimitExceeded

    async def _gen_factory(cancel_event, confirm_event):
        yield ProgressEvent(stage="metadata", progress=5, message="...")
        raise DurationLimitExceeded("Video is 4h0m, exceeding the 3h30m limit.")

    runner = TaskRunner()
    rt = runner.start(
        task_id=99003, user_id=1, platform="youtube",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ", gen_factory=_gen_factory,
    )

    import asyncio as _asyncio
    for _ in range(100):
        if rt.finished:
            break
        await _asyncio.sleep(0.01)

    error_events = [e for e in rt.events if e["event"] == "error"]
    assert error_events
    payload = json.loads(error_events[-1]["data"])
    assert payload["detail"] is not None
    assert payload["detail"].get("error_code") == "duration_exceeded"
