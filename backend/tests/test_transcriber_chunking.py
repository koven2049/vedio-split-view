"""Chunk splitting + no-speech handling in the ASR pipeline.

WHY: production repeatedly lost whole 1.5-hour podcasts to
``Fun-ASR failed: ASR_RESPONSE_HAVE_NO_WORDS``. Root cause chain:

1. ``math.ceil`` produced a tiny trailing chunk (5701s / 300s → a 1-second
   chunk 20) which is usually silence or outro music.
2. DashScope reports speechless audio as task FAILED, and that killed the
   whole job — discarding 19 chunks that transcribed fine.
3. Retrying re-hit the same dead chunk forever (earlier chunks come from
   cache), so the task could never finish.

These tests encode all three, plus the timeline invariant that offsets come
from split boundaries rather than the previous chunk's last sentence.
"""
from __future__ import annotations

import pytest

from video_split.service.downloader import SubtitleEntry
from video_split.service import transcriber


def test_no_speech_marker_detection():
    assert transcriber._is_no_speech("ASR_RESPONSE_HAVE_NO_WORDS", "")
    assert transcriber._is_no_speech("", "ASR_RESPONSE_HAVE_NO_WORDS")
    assert transcriber._is_no_speech("asr_response_have_no_words", "")
    assert not transcriber._is_no_speech("InvalidApiKey", "auth failed")
    assert not transcriber._is_no_speech("Throttling", "rate limited")


def test_short_tail_merged_into_previous_chunk(monkeypatch, tmp_path):
    """A 1-second remainder must not become its own chunk."""
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"x" * 2048)
    monkeypatch.setattr(transcriber, "get_audio_duration", lambda p: 5401.0)

    chunk_dir = tmp_path / "chunks"
    chunk_dir.mkdir()

    def _fake_run(cmd, **kwargs):
        for i in range(18):
            (chunk_dir / f"chunk_{i:03d}.mp3").write_bytes(b"y" * 2048)
        return None

    monkeypatch.setattr(transcriber.subprocess, "run", _fake_run)
    chunks = transcriber.split_audio(audio, chunk_seconds=300)

    assert len(chunks) == 18, "1s tail should merge into chunk 18, not add a 19th"


def test_long_tail_kept_as_own_chunk(monkeypatch, tmp_path):
    """A substantial remainder still gets its own chunk ��� no content dropped."""
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"x" * 2048)
    monkeypatch.setattr(transcriber, "get_audio_duration", lambda p: 5850.0)

    chunk_dir = tmp_path / "chunks"
    chunk_dir.mkdir()

    def _fake_run(cmd, **kwargs):
        for i in range(20):
            (chunk_dir / f"chunk_{i:03d}.mp3").write_bytes(b"y" * 2048)
        return None

    monkeypatch.setattr(transcriber.subprocess, "run", _fake_run)
    chunks = transcriber.split_audio(audio, chunk_seconds=300)
    assert len(chunks) == 20, "150s tail is real content and needs its own chunk"


@pytest.mark.asyncio
async def test_empty_chunk_does_not_stall_the_timeline(monkeypatch, tmp_path):
    """A speechless middle chunk must not shift later chunks onto stale offsets."""
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"x" * 2048)
    fake_chunks = []
    for i in range(3):
        p = tmp_path / f"chunk_{i:03d}.mp3"
        p.write_bytes(b"y" * 2048)
        fake_chunks.append(p)

    monkeypatch.setattr(transcriber, "split_audio", lambda p, **kw: fake_chunks)

    async def _fake_chunk(path, **kwargs):
        if path is fake_chunks[1]:
            return [], 0.0  # silence — degraded to an empty result
        return [SubtitleEntry(start=2.0, duration=1.0, text=path.name)], 10.0

    monkeypatch.setattr(transcriber, "transcribe_single_chunk", _fake_chunk)

    entries, usage = await transcriber.transcribe_audio(audio)

    assert [e.text for e in entries] == ["chunk_000.mp3", "chunk_002.mp3"]
    # chunk 2 sits at 2 * 300 + 2.0 ��� derived from the split boundary, so the
    # empty chunk in the middle costs no timeline accuracy.
    assert entries[0].start == pytest.approx(2.0)
    assert entries[1].start == pytest.approx(602.0)
    assert usage.duration_seconds == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_all_chunks_silent_raises(monkeypatch, tmp_path):
    """Zero speech anywhere is a real failure ��� never a silent empty transcript."""
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"x" * 2048)
    chunk = tmp_path / "chunk_000.mp3"
    chunk.write_bytes(b"y" * 2048)

    monkeypatch.setattr(transcriber, "split_audio", lambda p, **kw: [chunk, chunk])

    async def _silent(path, **kwargs):
        return [], 0.0

    monkeypatch.setattr(transcriber, "transcribe_single_chunk", _silent)

    with pytest.raises(RuntimeError, match="个音频块均未识别到语音"):
        await transcriber.transcribe_audio(audio)
