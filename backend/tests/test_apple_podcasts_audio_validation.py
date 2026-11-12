"""Apple Podcasts download integrity checks (Task #71: DRM'd stream).

Same failure family as the xiaoyuzhou Task #70 episodes — valid container
headers, ciphertext payloads — so the shared audio_integrity machinery is
wired in here too. These tests pin the apple-specific wiring: typed error
codes, mid-stream abort, one auto-retry for truncation, and the task_runner
broadcast of the code.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from video_split.service import apple_podcasts as ap
from video_split.service.downloader import MIN_AUDIO_BYTES


class _FakeCDN:
    """httpx.AsyncClient stand-in streaming enough bytes past the size floor."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url):
        class _Resp:
            status_code = 200
            headers: ClassVar[dict] = {"content-length": str(MIN_AUDIO_BYTES * 2)}

            def raise_for_status(self):
                return None

            async def aiter_bytes(self):
                yield b"\x00" * (MIN_AUDIO_BYTES * 2)

        class _StreamCtx:
            async def __aenter__(self):
                return _Resp()

            async def __aexit__(self, *exc):
                return False

        return _StreamCtx()


def _unlimited_cdn(state: dict, *, chunk_mb: int = 8, chunks: int = 25):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url):
            class _Resp:
                status_code = 200
                headers: ClassVar[dict] = {"content-length": str(chunk_mb * chunks * 1024 * 1024)}

                def raise_for_status(self):
                    return None

                async def aiter_bytes(self):
                    for _ in range(chunks):
                        state["pulled"] += chunk_mb * 1024 * 1024
                        yield b"\x00" * (chunk_mb * 1024 * 1024)

            class _StreamCtx:
                async def __aenter__(self):
                    return _Resp()

                async def __aexit__(self, *exc):
                    return False

            return _StreamCtx()

    return _Client


@pytest.mark.asyncio
async def test_undecodable_after_download_raises_typed(monkeypatch, tmp_path):
    """Small download (below thresholds) → post-download validation convicts."""
    monkeypatch.setattr(ap.httpx, "AsyncClient", _FakeCDN)

    from video_split.service.audio_integrity import UndecodableAudio

    def _fake_validate(path, expected):
        raise UndecodableAudio(head_decoded=0.16)

    monkeypatch.setattr(ap, "validate_decodable", _fake_validate)
    with pytest.raises(ap.ApplePodcastsError) as ei:
        await ap.download_apple_podcasts_audio(
            "https://cdn.example.com/e.mp3", tmp_path,
            expected_duration_seconds=5754,
        )
    assert ei.value.code == "audio_undecodable"


@pytest.mark.asyncio
async def test_truncated_retries_once_then_raises(monkeypatch, tmp_path):
    from video_split.service.audio_integrity import TruncatedDownload

    monkeypatch.setattr(ap.httpx, "AsyncClient", _FakeCDN)
    calls = {"n": 0}

    def _fake_validate(path, expected):
        calls["n"] += 1
        raise TruncatedDownload(start=2800.0, decoded=0.0)

    monkeypatch.setattr(ap, "validate_decodable", _fake_validate)
    with pytest.raises(ap.ApplePodcastsError) as ei:
        await ap.download_apple_podcasts_audio(
            "https://cdn.example.com/e.mp3", tmp_path,
            expected_duration_seconds=5754,
        )
    assert ei.value.code == "audio_truncated"
    assert calls["n"] == 2, "truncated download must retry exactly once"


@pytest.mark.asyncio
async def test_midstream_encrypted_aborts_early(monkeypatch, tmp_path):
    """Encrypted verdict at a threshold aborts before the stream drains."""
    state = {"pulled": 0}
    monkeypatch.setattr(ap.httpx, "AsyncClient", _unlimited_cdn(state))
    monkeypatch.setattr(ap, "early_probe", lambda path: "encrypted")
    with pytest.raises(ap.ApplePodcastsError) as ei:
        await ap.download_apple_podcasts_audio(
            "https://cdn.example.com/e.mp3", tmp_path,
            expected_duration_seconds=5754,
        )
    assert ei.value.code == "audio_undecodable"
    assert state["pulled"] == 8 * 1024 * 1024, "abort after the first threshold-crossing chunk"
    assert not list(tmp_path.glob("audio.*")), "aborted partial file must be cleaned"


@pytest.mark.asyncio
async def test_healthy_download_passes(monkeypatch, tmp_path):
    monkeypatch.setattr(ap.httpx, "AsyncClient", _FakeCDN)
    monkeypatch.setattr(ap, "validate_decodable", lambda path, expected: None)
    out = await ap.download_apple_podcasts_audio(
        "https://cdn.example.com/e.m4a", tmp_path,
        expected_duration_seconds=5754,
    )
    assert out.name == "audio.m4a" and out.exists()


# ---------------------------------------------------------------------------
# task_runner: error_code propagation for the apple platform
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_runner_propagates_apple_error_code():
    """ApplePodcastsError must broadcast error_code like xiaoyuzhou's do."""
    import asyncio as _asyncio
    import json

    from video_split.schemas import ProgressEvent
    from video_split.service.task_runner import TaskRunner

    async def _gen_factory(cancel_event, confirm_event):
        yield ProgressEvent(stage="metadata", progress=5, message="...")
        raise ap.ApplePodcastsError("audio_undecodable", "fake DRM")

    runner = TaskRunner()
    rt = runner.start(
        task_id=99002,
        user_id=1,
        platform="apple_podcasts",
        url="https://podcasts.apple.com/x/id1?i=2",
        gen_factory=_gen_factory,
    )
    for _ in range(100):
        if rt.finished:
            break
        await _asyncio.sleep(0.01)

    error_events = [e for e in rt.events if e["event"] == "error"]
    assert error_events, "expected an error event"
    payload = json.loads(error_events[-1]["data"])
    assert payload["detail"]["error_code"] == "audio_undecodable"
