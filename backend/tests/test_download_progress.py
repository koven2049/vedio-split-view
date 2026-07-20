"""Tests for the download progress relay helper.

Covers:
- progressive ratio callbacks → monotonic ProgressEvent stream
- throttle: high-frequency callbacks yield fewer events than callbacks
- exception propagation from download coroutine
- total unknown (ratio=0 fallback path doesn't crash)
"""
from __future__ import annotations

import asyncio

import pytest

from video_split.schemas import ProgressEvent
from video_split.service.video_service import _relay_download_progress


def _factory_streaming(ratios: list[float]):
    """Build a factory that invokes progress_callback with the given ratios."""
    ratios_iter = list(ratios)

    async def download_coro(*, progress_callback=None):
        for r in ratios_iter:
            if progress_callback:
                progress_callback(r)
            await asyncio.sleep(0)
        return "ok"

    return lambda *, progress_callback=None: download_coro(progress_callback=progress_callback)


@pytest.mark.asyncio
async def test_relay_progressive():
    """Successive ratio callbacks yield ProgressEvents with monotonically
    increasing progress, bounded by [base, base+span]."""
    ratios = [0.0, 0.25, 0.5, 0.75, 1.0]
    factory = _factory_streaming(ratios)

    events: list[ProgressEvent] = []
    async for ev in _relay_download_progress(
        "audio_download", base_pct=15, span_pct=40, factory=factory,
    ):
        events.append(ev)

    assert events, "relay should yield at least one event"
    # first event sits at base
    assert events[0].progress == 15
    # last event lands at base+span (收尾)
    assert events[-1].progress == 55
    # monotonic non-decreasing
    progresses = [e.progress for e in events]
    assert progresses == sorted(progresses), f"non-monotonic: {progresses}"
    # all within [15, 55]
    assert all(15 <= p <= 55 for p in progresses)


@pytest.mark.asyncio
async def test_relay_throttle():
    """High-frequency callbacks (tiny increments) produce fewer yields than
    the raw callback count, thanks to the ≥1% / ≥0.5s throttle."""
    # 50 callbacks of 0.001 increment each → 5% total span, many sub-1% jumps
    ratios = [round(i * 0.001, 4) for i in range(1, 51)]
    callback_count = len(ratios)
    factory = _factory_streaming(ratios)

    yields = 0
    async for _ in _relay_download_progress(
        "audio_download", base_pct=15, span_pct=40, factory=factory,
    ):
        yields += 1

    assert yields < callback_count, (
        f"throttle should reduce yields: got {yields} yields for {callback_count} callbacks"
    )
    # still produces a reasonable number of events (not just 1)
    assert yields >= 2


@pytest.mark.asyncio
async def test_relay_propagates_exception():
    """If the download coroutine raises, the relay propagates the exception
    after draining the in-flight progress events."""

    async def download_coro(*, progress_callback=None):
        if progress_callback:
            progress_callback(0.3)
        await asyncio.sleep(0)
        raise RuntimeError("download exploded")

    factory = lambda *, progress_callback=None: download_coro(progress_callback=progress_callback)

    with pytest.raises(RuntimeError, match="download exploded"):
        async for _ in _relay_download_progress(
            "audio_download", base_pct=15, span_pct=40, factory=factory,
        ):
            pass


@pytest.mark.asyncio
async def test_relay_total_unknown():
    """When total bytes unknown (ratio stays 0 / no callbacks), relay still
    terminates cleanly and emits the closing event at base+span."""
    # download completes without ever invoking callback (e.g. content-length missing
    # and downloader skips callbacks)
    async def download_coro(*, progress_callback=None):
        await asyncio.sleep(0)
        return "ok"

    factory = lambda *, progress_callback=None: download_coro(progress_callback=progress_callback)

    events: list[ProgressEvent] = []
    async for ev in _relay_download_progress(
        "audio_download", base_pct=15, span_pct=40, factory=factory,
        label="下载音频",
    ):
        events.append(ev)

    # at least the opening (base) and closing (base+span) events
    assert events[0].progress == 15
    assert events[-1].progress == 55
    # message degrades gracefully (no crash, contains label)
    assert "下载音频" in events[-1].message
