"""Download-time decode validation for xiaoyuzhou audio.

WHY: Task #70 — the CDN served a 130 MB "MP3" whose payload was ciphertext
(one plaintext frame up front). It passed the size floor, ffprobe reported
the claimed 10875s (size÷bitrate estimate), and only the split-stage guard
caught it — after the user had already been told to "re-download and retry",
which provably returns identical bytes. These tests pin the new behavior:
head-window garbage → ``audio_undecodable`` (no retry), middle/tail window
failure → one automatic retry, then ``audio_truncated``.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from video_split.service import xiaoyuzhou as xy
from video_split.service.downloader import MIN_AUDIO_BYTES
from video_split.service.transcriber import probe_decoded_seconds

_ffmpeg_available = shutil.which("ffmpeg") and shutil.which("ffprobe")
requires_ffmpeg = pytest.mark.skipif(not _ffmpeg_available, reason="ffmpeg/ffprobe not installed")


# ---------------------------------------------------------------------------
# probe_decoded_seconds (real ffmpeg)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def healthy_mp3(tmp_path_factory):
    """A real 30s MP3 — decodes fully, unlike the encrypted-CDN failure mode."""
    path = tmp_path_factory.mktemp("audio") / "healthy.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
         "-codec:a", "libmp3lame", "-b:a", "64k", str(path)],
        check=True,
    )
    return path


@requires_ffmpeg
def test_probe_decodes_full_window_on_healthy_file(healthy_mp3):
    assert probe_decoded_seconds(healthy_mp3, start=0.0, window=8.0) >= 7.0
    assert probe_decoded_seconds(healthy_mp3, start=10.0, window=8.0) >= 7.0


@requires_ffmpeg
def test_probe_returns_zero_for_garbage(tmp_path):
    """Ciphertext/random bytes behind one frame — the Task #70 signature."""
    import os
    garbage = tmp_path / "encrypted.mp3"
    garbage.write_bytes(os.urandom(256 * 1024))
    assert probe_decoded_seconds(garbage, start=0.0, window=8.0) < 1.0


@requires_ffmpeg
def test_probe_returns_zero_seeking_past_eof(healthy_mp3):
    """A truncated download has no tail: a window beyond EOF decodes nothing."""
    assert probe_decoded_seconds(healthy_mp3, start=1000.0, window=8.0) == 0.0


# ---------------------------------------------------------------------------
# window placement
# ---------------------------------------------------------------------------


def test_probe_start_offsets_scale_with_duration():
    assert xy._probe_start_offsets(0) == [0.0]            # unknown duration: head only
    assert xy._probe_start_offsets(30) == [0.0]           # short episode: head covers it
    mid = xy._probe_start_offsets(120)
    assert mid[0] == 0.0 and len(mid) == 2 and 55 <= mid[1] <= 65
    long_eps = xy._probe_start_offsets(10875)
    assert len(long_eps) == 3
    # tail keeps a 90s margin behind the claimed end (minute-rounded metadata)
    assert 60.0 <= long_eps[2] <= 10875 - 90 + 1


# ---------------------------------------------------------------------------
# download_xiaoyuzhou_audio classification + retry
# ---------------------------------------------------------------------------


class _FakeCDN:
    """httpx.AsyncClient stand-in that streams enough bytes past the size floor."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url):
        class _Resp:
            status_code = 200
            headers = {"content-length": str(MIN_AUDIO_BYTES * 2)}

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


@pytest.mark.asyncio
async def test_undecodable_head_fails_without_retry(monkeypatch, tmp_path):
    """Encrypted content is deterministic — retrying the same URL is a no-op."""
    monkeypatch.setattr(xy.httpx, "AsyncClient", _FakeCDN)
    attempts = []

    def _probe(path, *, start, window):
        attempts.append(start)
        return 0.2 if start == 0.0 else 0.0

    monkeypatch.setattr(xy, "probe_decoded_seconds", _probe)

    with pytest.raises(xy.XiaoyuzhouError) as ei:
        await xy.download_xiaoyuzhou_audio(
            "https://cdn.example.com/e.mp3", tmp_path,
            expected_duration_seconds=10875,
        )
    assert ei.value.code == "audio_undecodable"
    assert attempts == [0.0], "undecodable must fail on the first download attempt"


@pytest.mark.asyncio
async def test_truncated_retries_once_then_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(xy.httpx, "AsyncClient", _FakeCDN)
    attempts = []

    def _probe(path, *, start, window):
        # head fine, tail missing — network truncation signature
        return 8.0 if start == 0.0 else 0.0

    monkeypatch.setattr(xy, "probe_decoded_seconds", _probe)
    real_validate = xy._validate_decodable

    def _counted_validate(path, expected):
        attempts.append(path.read_bytes()[:1])  # one entry per download attempt
        return real_validate(path, expected)

    monkeypatch.setattr(xy, "_validate_decodable", _counted_validate)

    with pytest.raises(xy.XiaoyuzhouError) as ei:
        await xy.download_xiaoyuzhou_audio(
            "https://cdn.example.com/e.mp3", tmp_path,
            expected_duration_seconds=10875,
        )
    assert ei.value.code == "audio_truncated"
    assert len(attempts) == 2, "truncated download must retry exactly once"


@pytest.mark.asyncio
async def test_truncated_retry_can_succeed(monkeypatch, tmp_path):
    monkeypatch.setattr(xy.httpx, "AsyncClient", _FakeCDN)
    calls = {"n": 0}

    def _probe(path, *, start, window):
        if start == 0.0:
            return 8.0
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 8.0  # first tail probe fails, second passes

    monkeypatch.setattr(xy, "probe_decoded_seconds", _probe)

    out = await xy.download_xiaoyuzhou_audio(
        "https://cdn.example.com/e.mp3", tmp_path,
        expected_duration_seconds=10875,
    )
    assert out.exists() and out.stat().st_size >= MIN_AUDIO_BYTES


@pytest.mark.asyncio
async def test_healthy_download_passes_validation(monkeypatch, tmp_path):
    monkeypatch.setattr(xy.httpx, "AsyncClient", _FakeCDN)
    monkeypatch.setattr(
        xy, "probe_decoded_seconds",
        lambda path, *, start, window: 8.0,
    )
    out = await xy.download_xiaoyuzhou_audio(
        "https://cdn.example.com/e.mp3", tmp_path,
        expected_duration_seconds=3600,
    )
    assert out.exists()
