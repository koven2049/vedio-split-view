"""LLM 429 rate-limit retry behavior for analyzer and brainstorm.

Why: BigModel returns 429 on rate limits after a long (expensive) transcription
has already succeeded; failing the whole task wastes the transcription. A short
exponential backoff usually rides out the limit window.
"""
from __future__ import annotations

import json

import httpx
import pytest

from video_split.service import analyzer as analyzer_mod
from video_split.service import brainstorm as brainstorm_mod
from video_split.service.downloader import SubtitleEntry
from video_split.service.llm_http import LLMInsufficientBalanceError


def _ok_llm_response() -> dict:
    content = json.dumps({"summary": "s", "segments": []})
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class _FlakyClient:
    """httpx.AsyncClient stand-in: N failures, then 200.

    fail_body lets callers return BigModel's 余额不足 body (code 1113) to
    exercise the non-retryable path.
    """

    def __init__(self, failures: int, calls: list[int], fail_body: str = "rate limited"):
        self._failures = failures
        self._calls = calls
        self._fail_body = fail_body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kw):
        self._calls.append(1)
        req = httpx.Request("POST", url)
        if len(self._calls) <= self._failures:
            return httpx.Response(429, request=req, text=self._fail_body)
        return httpx.Response(200, request=req, json=_ok_llm_response())


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    import asyncio as _asyncio

    async def _no_sleep(_s):
        return None
    # analyzer/brainstorm 各自模块内的 asyncio.sleep；brainstorm 若未 import asyncio
    # (实现补重试时会加)，则退回 patch 全局 asyncio.sleep
    for mod in (analyzer_mod, brainstorm_mod):
        if hasattr(mod, "asyncio"):
            monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(_asyncio, "sleep", _no_sleep)


@pytest.fixture
def _llm_config(test_config_path):
    from video_split.config import set_config_path
    set_config_path(test_config_path)


@pytest.mark.asyncio
async def test_analyzer_retries_429_then_succeeds(monkeypatch, _llm_config):
    calls: list[int] = []
    monkeypatch.setattr(
        analyzer_mod.httpx, "AsyncClient",
        lambda **kw: _FlakyClient(failures=2, calls=calls),
    )
    subs = [SubtitleEntry(start=0.0, duration=5.0, text="hello")]

    result = await analyzer_mod.analyze_transcript(subs, duration_seconds=30)

    assert result.summary == "s"
    assert len(calls) == 3  # two 429s + one success


@pytest.mark.asyncio
async def test_analyzer_429_exhausts_and_raises(monkeypatch, _llm_config):
    calls: list[int] = []
    monkeypatch.setattr(
        analyzer_mod.httpx, "AsyncClient",
        lambda **kw: _FlakyClient(failures=99, calls=calls),
    )
    subs = [SubtitleEntry(start=0.0, duration=5.0, text="hello")]

    with pytest.raises(httpx.HTTPStatusError):
        await analyzer_mod.analyze_transcript(subs, duration_seconds=30)

    assert len(calls) == 3  # retried up to max, then raised


@pytest.mark.asyncio
async def test_brainstorm_retries_429_then_succeeds(monkeypatch, _llm_config):
    calls: list[int] = []
    monkeypatch.setattr(
        brainstorm_mod.httpx, "AsyncClient",
        lambda **kw: _FlakyClient(failures=2, calls=calls),
    )

    result, usage = await brainstorm_mod._call_llm("test prompt")

    assert result == {"summary": "s", "segments": []}
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_analyzer_insufficient_balance_does_not_retry(monkeypatch, _llm_config):
    """BigModel code 1113 (余额不足) is deterministic — retrying won't help.
    Fail fast on the first attempt with a clear Chinese error so the user
    knows to top up, instead of burning 2s/4s backoff on every request."""
    calls: list[int] = []
    body = '{"error":{"code":"1113","message":"余额不足或无可用资源包,请充值。"}}'
    monkeypatch.setattr(
        analyzer_mod.httpx, "AsyncClient",
        lambda **kw: _FlakyClient(failures=99, calls=calls, fail_body=body),
    )
    subs = [SubtitleEntry(start=0.0, duration=5.0, text="hello")]

    with pytest.raises(LLMInsufficientBalanceError, match="余额不足"):
        await analyzer_mod.analyze_transcript(subs, duration_seconds=30)

    assert len(calls) == 1  # no retry — failed fast
