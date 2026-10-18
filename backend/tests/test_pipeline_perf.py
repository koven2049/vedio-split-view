"""Pipeline performance batch: speaker-preserving merge, parallel translation, WAL."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from video_split.service.downloader import SubtitleEntry
from video_split.service import analyzer, transcriber


@pytest.fixture
def _config(test_config_path):
    from video_split.config import set_config_path

    set_config_path(test_config_path)


# ─── transcribe_audio: parallel merge keeps speaker labels ───


@pytest.mark.asyncio
async def test_transcribe_merge_preserves_speaker(monkeypatch, tmp_path, _config):
    """Chunk merge used to rebuild entries without the speaker field — any
    ASR diarization labels died exactly here."""
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"x" * 2048)

    async def _fake_single(chunk_path, progress_callback=None, chunk_index=1, total_chunks=1):
        entries = [
            SubtitleEntry(start=0.0, duration=2.0, text="你好。", speaker="0"),
            SubtitleEntry(start=3.0, duration=2.0, text="谢谢。", speaker="1"),
        ]
        return entries, 5.0

    monkeypatch.setattr(transcriber, "split_audio", lambda p: [p])
    monkeypatch.setattr(transcriber, "transcribe_single_chunk", _fake_single)

    entries, usage = await transcriber.transcribe_audio(audio)
    assert [e.speaker for e in entries] == ["0", "1"]
    assert usage.model  # populated from settings


@pytest.mark.asyncio
async def test_transcribe_chunks_run_and_merge_in_order(monkeypatch, tmp_path, _config):
    """Chunks execute concurrently but results merge in chunk order with offsets."""
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"x" * 2048)
    chunks = [tmp_path / "c1.mp3", tmp_path / "c2.mp3"]
    for c in chunks:
        c.write_bytes(b"y" * 1024)

    running: list[int] = []

    async def _fake_single(chunk_path, progress_callback=None, chunk_index=1, total_chunks=2):
        running.append(chunk_index)
        await asyncio.sleep(0.05)  # let both chunks overlap
        running.remove(chunk_index)
        return [SubtitleEntry(start=1.0, duration=1.0, text=f"块{chunk_index}", speaker="0")], 10.0

    monkeypatch.setattr(transcriber, "split_audio", lambda p: chunks)
    monkeypatch.setattr(transcriber, "transcribe_single_chunk", _fake_single)

    entries, _usage = await transcriber.transcribe_audio(audio)
    assert len(running) == 0
    # offsets by chunk index, order preserved despite concurrent completion
    assert [(e.start, e.text) for e in entries] == [(1.0, "块1"), (301.0, "块2")]


# ─── translation: concurrency + per-batch disk checkpoint ───


def _en_subs(n: int) -> list[SubtitleEntry]:
    return [
        SubtitleEntry(start=i * 2.0, duration=1.5, text=f"line {i}", speaker="0") for i in range(n)
    ]


@pytest.mark.asyncio
async def test_translate_parallel_batches_keep_order_and_speaker(
    monkeypatch, test_config_path, tmp_path
):
    from video_split.config import set_config_path

    set_config_path(test_config_path)

    calls: list[list[str]] = []
    active: list[int] = []
    peak: list[int] = [0]

    async def _fake_post_chat(url, payload, headers, timeout):
        active.append(1)
        peak[0] = max(peak[0], len(active))
        await asyncio.sleep(0.03)
        active.pop()
        keys = []
        for t in payload["messages"][0]["content"].split("\n"):
            m = re.match(r"\[(\d+)\]", t.strip())
            if m:
                keys.append(m.group(1))
        calls.append(keys)
        return {
            "choices": [{"message": {"content": json.dumps({k: f"译文{int(k)}" for k in keys})}}],
            "usage": {},
        }

    monkeypatch.setattr(analyzer, "post_chat", _fake_post_chat)
    # isolate cache dir to tmp
    monkeypatch.setattr(analyzer, "_translate_cache_path", lambda texts: tmp_path / "tc.json")

    subs = _en_subs(120)  # 3 batches of 50
    out = await analyzer.translate_subtitles_to_chinese(subs)
    assert [e.text for e in out] == [f"译文{i % 50 + 1}" for i in range(120)]
    assert all(e.speaker == "0" for e in out), "translation must preserve speaker"
    assert len(calls) == 3
    assert peak[0] > 1, "batches should overlap (concurrency)"


@pytest.mark.asyncio
async def test_translate_batch_cache_skips_done_work(monkeypatch, test_config_path, tmp_path):
    from video_split.config import set_config_path

    set_config_path(test_config_path)
    calls: list[int] = []

    async def _fake_post_chat(url, payload, headers, timeout):
        calls.append(1)
        raise RuntimeError("llm down")

    monkeypatch.setattr(analyzer, "post_chat", _fake_post_chat)

    cache = tmp_path / f"tc_{abs(hash('x'))}.json"
    monkeypatch.setattr(
        analyzer,
        "_translate_cache_path",
        lambda texts: tmp_path / f"tc{len(texts)}.json",
    )
    # pre-seed the cache for the single 2-line batch
    (tmp_path / "tc2.json").write_text(
        json.dumps(["甲", "乙"], ensure_ascii=False), encoding="utf-8"
    )

    subs = [
        SubtitleEntry(start=0, duration=1, text="a", speaker=""),
        SubtitleEntry(start=1, duration=1, text="b", speaker=""),
    ]
    out = await analyzer.translate_subtitles_to_chinese(subs)
    assert [e.text for e in out] == ["甲", "乙"]
    assert calls == [], "cached batch must not hit the LLM"


# ─── SQLite pragmas ───


@pytest.mark.asyncio
async def test_sqlite_busy_timeout_set(test_config_path):
    """busy_timeout yes; journal_mode must stay default — WAL breaks on
    podman virtiofs bind mounts (disk I/O error, see database.py note)."""
    from sqlalchemy import text

    from video_split.config import set_config_path
    from video_split.database import _get_session_factory

    set_config_path(test_config_path)
    factory = _get_session_factory()
    async with factory() as session:
        mode = (await session.execute(text("PRAGMA journal_mode"))).scalar()
        timeout = (await session.execute(text("PRAGMA busy_timeout"))).scalar()
    assert str(mode).lower() == "delete"
    assert int(timeout) >= 5000
