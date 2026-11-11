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
from typing import ClassVar

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


# ---------------------------------------------------------------------------
# mid-stream early abort (traffic saver for encrypted episodes)
# ---------------------------------------------------------------------------


@requires_ffmpeg
def test_early_probe_ok_on_healthy_prefix(healthy_mp3, tmp_path):
    """A healthy file's head decodes fully → verdict "ok"."""
    prefix = tmp_path / "prefix.mp3"
    prefix.write_bytes(healthy_mp3.read_bytes())
    assert xy._early_probe(prefix) == "ok"


@requires_ffmpeg
def test_early_probe_flags_plaintext_frame_plus_ciphertext(healthy_mp3, tmp_path):
    """One plaintext frame then random bytes — the exact Task #70 signature."""
    import os
    encrypted_like = tmp_path / "encrypted.mp3"
    # keep the first few real frames (≥1 frame ≈ 400B at 64kbps), fill the
    # rest with ciphertext
    encrypted_like.write_bytes(healthy_mp3.read_bytes()[:600] + os.urandom(2 * 1024 * 1024))
    assert xy._early_probe(encrypted_like) == "encrypted"


@requires_ffmpeg
def test_early_probe_unknown_when_still_inside_id3_art(tmp_path):
    """A prefix entirely inside a giant cover-art tag is inconclusive, not
    encrypted — the ID3 header itself declares the tag size."""
    size = 8 * 1024 * 1024  # declared tag bigger than the 1MB prefix below
    head = b"ID3" + bytes([3, 0, 0]) + bytes(
        [(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F]
    )
    prefix = tmp_path / "art_only.mp3"
    prefix.write_bytes(head + b"\x00" * (1024 * 1024 - 10))
    assert xy._early_probe(prefix) == "unknown"


@requires_ffmpeg
def test_early_probe_never_flags_moov_at_end_m4a_prefix(tmp_path):
    """An MP4 prefix without its (end-of-file) moov atom must never be called
    encrypted — ffmpeg ≥7 either decodes the mdat directly ("ok") or refuses
    to open the container ("unknown"); both continue the download and defer
    to the post-download validation."""
    whole = tmp_path / "tail_moov.m4a"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
         "-codec:a", "aac", str(whole)],  # no -movflags faststart → moov at end
        check=True,
    )
    prefix = tmp_path / "prefix.m4a"
    prefix.write_bytes(whole.read_bytes()[:256 * 1024])
    assert xy._early_probe(prefix) in ("ok", "unknown")


def _unlimited_cdn(state: dict, *, chunk_mb: int = 8, chunks: int = 25):
    """httpx.AsyncClient stand-in streaming fixed-size chunks; records how
    many bytes were actually pulled from the wire."""

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
async def test_download_aborts_early_on_encrypted(monkeypatch, tmp_path):
    """Encrypted verdict mid-stream kills the download at ~4MB, not 200MB."""
    state = {"pulled": 0}
    monkeypatch.setattr(xy.httpx, "AsyncClient", _unlimited_cdn(state))
    monkeypatch.setattr(xy, "_early_probe", lambda path: "encrypted")

    with pytest.raises(xy.XiaoyuzhouError) as ei:
        await xy.download_xiaoyuzhou_audio(
            "https://cdn.example.com/e.mp3", tmp_path,
            expected_duration_seconds=10875,
        )
    assert ei.value.code == "audio_undecodable"
    # 4MB threshold crossed on the first 8MB chunk — must not pull a second one
    assert state["pulled"] == 8 * 1024 * 1024, (
        f"must abort after the first threshold-crossing chunk, pulled {state['pulled']}"
    )
    assert not (tmp_path / "audio.m4a").exists(), "aborted partial file must be cleaned"


@pytest.mark.asyncio
async def test_download_early_unknown_falls_through(monkeypatch, tmp_path):
    """"unknown" (moov-at-end / art-only) must NOT abort — the full download
    proceeds and the post-download validation makes the final call."""
    state = {"pulled": 0}
    monkeypatch.setattr(xy.httpx, "AsyncClient", _unlimited_cdn(state, chunk_mb=3, chunks=2))
    probes = []
    monkeypatch.setattr(xy, "_early_probe", lambda path: probes.append(1) or "unknown")
    monkeypatch.setattr(
        xy, "probe_decoded_seconds",
        lambda path, *, start, window: 8.0,
    )
    out = await xy.download_xiaoyuzhou_audio(
        "https://cdn.example.com/e.mp3", tmp_path,
        expected_duration_seconds=3600,
    )
    assert out.exists()
    assert probes, "early probe must have run"
    assert state["pulled"] == 6 * 1024 * 1024, "unknown verdict must not abort the stream"
