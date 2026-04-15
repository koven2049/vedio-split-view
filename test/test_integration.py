"""Integration tests using real APIs (Fun-ASR + GLM-4.7) with config/app.yaml.

These tests call external services and consume API credits.
Run from the project root:
    python -m pytest test/test_integration.py -v -s

Test data (in test/ directory):
    audio.mp3                    — source audio file (~20 min)
    chunk_000.transcript.json    — pre-computed transcript for chunk 0
    chunk_001.transcript.json    — pre-computed transcript for chunk 1
    chunk_002.transcript.json    — pre-computed transcript for chunk 2
    chunk_003.transcript.json    — pre-computed transcript for chunk 3
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend" / "src"))

from video_split.config import set_config_path, get_settings  # noqa: E402

set_config_path(str(PROJECT_ROOT / "config" / "app.yaml"))

from video_split.service.downloader import SubtitleEntry  # noqa: E402
from video_split.service.analyzer import analyze_transcript, _parse_llm_response  # noqa: E402
from video_split.service.transcriber import (  # noqa: E402
    transcribe_single_chunk,
    transcribe_audio,
    split_audio,
    get_audio_duration,
    _is_dashscope_funasr,
)

TEST_DIR = Path(__file__).resolve().parent
AUDIO_FILE = TEST_DIR / "audio.mp3"
CHUNK_FILES = sorted(TEST_DIR.glob("chunk_*.transcript.json"))

HAS_FFPROBE = shutil.which("ffprobe") is not None
requires_ffprobe = pytest.mark.skipif(not HAS_FFPROBE, reason="ffprobe not installed")


def _load_transcript_json(path: Path) -> list[SubtitleEntry]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [SubtitleEntry(start=e["start"], duration=e["duration"], text=e["text"]) for e in data]


def _merge_all_transcripts() -> tuple[list[SubtitleEntry], float]:
    """Load and merge all chunk transcript JSONs, returning entries + total duration."""
    all_entries: list[SubtitleEntry] = []
    time_offset = 0.0
    for chunk_file in CHUNK_FILES:
        entries = _load_transcript_json(chunk_file)
        for e in entries:
            all_entries.append(SubtitleEntry(start=e.start + time_offset, duration=e.duration, text=e.text))
        if entries:
            last = entries[-1]
            time_offset += last.start + last.duration + 0.5
    total_duration = max(e.start + e.duration for e in all_entries) if all_entries else 0
    return all_entries, total_duration


# ---------------------------------------------------------------------------
# Transcription tests (Fun-ASR + OSS)
# ---------------------------------------------------------------------------

class TestTranscription:
    """Tests that call the real ASR API."""

    @requires_ffprobe
    def test_audio_duration(self):
        if not AUDIO_FILE.exists():
            pytest.skip("audio.mp3 not found")
        duration = get_audio_duration(AUDIO_FILE)
        assert duration > 60, f"Expected > 60s, got {duration:.1f}s"
        print(f"\nAudio duration: {duration:.1f}s (~{duration / 60:.1f} min)")

    @requires_ffprobe
    def test_split_audio(self):
        if not AUDIO_FILE.exists():
            pytest.skip("audio.mp3 not found")
        chunks = split_audio(AUDIO_FILE)
        assert len(chunks) >= 1
        print(f"\nSplit into {len(chunks)} chunks")
        for i, c in enumerate(chunks):
            size_mb = c.stat().st_size / (1024 * 1024)
            print(f"  chunk {i}: {c.name} ({size_mb:.1f} MB)")

    @pytest.mark.asyncio
    async def test_transcribe_single_chunk(self):
        """Transcribe the full audio file as a single chunk via real ASR API.

        This bypasses ffprobe/split — it sends audio.mp3 directly to the ASR.
        The transcript cache (audio.transcript.json) is preserved for reuse.
        """
        if not AUDIO_FILE.exists():
            pytest.skip("audio.mp3 not found")

        settings = get_settings()
        is_funasr = _is_dashscope_funasr()
        print(f"\nASR model: {settings.transcription.model} (Fun-ASR: {is_funasr})")
        if is_funasr and not settings.oss.enabled:
            pytest.skip("Fun-ASR requires OSS config")

        size_mb = AUDIO_FILE.stat().st_size / (1024 * 1024)
        print(f"Transcribing: {AUDIO_FILE.name} ({size_mb:.1f} MB)")

        entries = await transcribe_single_chunk(AUDIO_FILE)
        assert len(entries) > 0, "Expected at least 1 transcript segment"
        print(f"Got {len(entries)} segments")
        for e in entries[:5]:
            print(f"  [{e.start:.1f}s] {e.text[:80]}")
        if len(entries) > 5:
            print(f"  ... and {len(entries) - 5} more")

    @requires_ffprobe
    @pytest.mark.asyncio
    async def test_transcribe_full_audio(self):
        """Transcribe full audio file (chunk-by-chunk) via real ASR API.

        Requires ffprobe/ffmpeg for splitting.
        """
        if not AUDIO_FILE.exists():
            pytest.skip("audio.mp3 not found")

        settings = get_settings()
        if _is_dashscope_funasr() and not settings.oss.enabled:
            pytest.skip("Fun-ASR requires OSS config")

        entries = await transcribe_audio(AUDIO_FILE)
        assert len(entries) > 10, f"Expected > 10 segments, got {len(entries)}"

        total_duration = max(e.start + e.duration for e in entries)
        print(f"\nFull transcription: {len(entries)} segments, ~{total_duration:.0f}s")
        for e in entries[:3]:
            print(f"  [{e.start:.1f}s] {e.text[:80]}")
        print(f"  ...")
        for e in entries[-3:]:
            print(f"  [{e.start:.1f}s] {e.text[:80]}")


# ---------------------------------------------------------------------------
# LLM analysis tests (GLM-4.7)
# ---------------------------------------------------------------------------

class TestLLMAnalysis:
    """Tests that call the real LLM API using pre-existing transcript JSONs."""

    @pytest.fixture(autouse=True)
    def _check_transcripts(self):
        if not CHUNK_FILES:
            pytest.skip("No chunk transcript JSONs found in test/")

    def test_load_transcripts(self):
        entries, duration = _merge_all_transcripts()
        assert len(entries) > 50, f"Expected > 50 entries, got {len(entries)}"
        assert duration > 600, f"Expected > 600s duration, got {duration:.0f}s"
        print(f"\nLoaded {len(entries)} entries, ~{duration:.0f}s ({duration / 60:.1f} min)")

    @pytest.mark.asyncio
    async def test_analyze_transcript(self):
        """Full LLM analysis using merged transcript data (~20 min video)."""
        entries, duration = _merge_all_transcripts()
        settings = get_settings()
        print(f"\nLLM model: {settings.llm.model}")
        print(f"Transcript: {len(entries)} entries, ~{duration:.0f}s")

        result = await analyze_transcript(entries, int(duration))

        assert result.summary, "Summary should not be empty"
        assert len(result.segments) >= 2, f"Expected >= 2 segments, got {len(result.segments)}"

        print(f"\nSummary: {result.summary[:200]}")
        print(f"Segments ({len(result.segments)}):")
        for seg in result.segments:
            m1, s1 = divmod(seg.start_seconds, 60)
            m2, s2 = divmod(seg.end_seconds, 60)
            print(f"  [{m1:02d}:{s1:02d} - {m2:02d}:{s2:02d}] {seg.title}")

        last_end = 0
        for seg in result.segments:
            assert seg.start_seconds >= 0
            assert seg.end_seconds > seg.start_seconds
            assert seg.start_seconds >= last_end - 5, f"Segments overlap: {seg.start_seconds} < {last_end}"
            assert seg.title, "Segment title should not be empty"
            last_end = seg.end_seconds

    @pytest.mark.asyncio
    async def test_analyze_single_chunk(self):
        """LLM analysis with a single chunk transcript (~5 min)."""
        entries = _load_transcript_json(CHUNK_FILES[0])
        duration = max(e.start + e.duration for e in entries)
        print(f"\nSingle chunk: {len(entries)} entries, ~{duration:.0f}s")

        result = await analyze_transcript(entries, int(duration))
        assert result.summary
        assert len(result.segments) >= 1
        print(f"Summary: {result.summary[:150]}")
        print(f"Segments: {len(result.segments)}")


# ---------------------------------------------------------------------------
# Offline parsing tests (no API calls, no external tools)
# ---------------------------------------------------------------------------

class TestOfflineParsing:
    """Pure offline tests for transcript loading and LLM response parsing."""

    def test_transcript_json_structure(self):
        for f in CHUNK_FILES:
            data = json.loads(f.read_text(encoding="utf-8"))
            assert isinstance(data, list), f"{f.name} should be a JSON array"
            for entry in data:
                assert "start" in entry, f"Missing 'start' in {f.name}"
                assert "duration" in entry, f"Missing 'duration' in {f.name}"
                assert "text" in entry, f"Missing 'text' in {f.name}"
                assert entry["duration"] > 0, f"Duration <= 0 in {f.name}"

    def test_merge_transcripts_order(self):
        entries, _ = _merge_all_transcripts()
        for i in range(1, len(entries)):
            assert entries[i].start >= entries[i - 1].start, (
                f"Entry {i} start ({entries[i].start}) < entry {i-1} start ({entries[i-1].start})"
            )

    def test_merge_transcripts_coverage(self):
        entries, duration = _merge_all_transcripts()
        assert len(entries) == 205, f"Expected 205 total entries, got {len(entries)}"
        assert 1100 < duration < 1300, f"Expected ~1198s duration, got {duration:.0f}s"

    def test_parse_llm_json_response(self):
        raw = json.dumps({
            "summary": "Test summary",
            "segments": [
                {"index": 0, "title": "Part 1", "summary": "First part", "start_seconds": 0, "end_seconds": 300},
                {"index": 1, "title": "Part 2", "summary": "Second part", "start_seconds": 300, "end_seconds": 600},
            ],
        })
        result = _parse_llm_response(raw)
        assert result.summary == "Test summary"
        assert len(result.segments) == 2
        assert result.segments[0].end_seconds == 300
        assert result.segments[1].start_seconds == 300

    def test_parse_llm_json_in_markdown(self):
        raw = """Here is the analysis:
```json
{
  "summary": "Video about testing",
  "segments": [
    {"index": 0, "title": "Intro", "summary": "Introduction", "start_seconds": 0, "end_seconds": 180}
  ]
}
```"""
        result = _parse_llm_response(raw)
        assert result.summary == "Video about testing"
        assert len(result.segments) == 1

    def test_parse_llm_no_json_raises(self):
        with pytest.raises(ValueError, match="does not contain valid JSON"):
            _parse_llm_response("This is plain text without JSON")


# ---------------------------------------------------------------------------
# Platform detection tests
# ---------------------------------------------------------------------------

class TestPlatformDetection:
    """Verify URL → (platform, video_id) mapping for all supported platforms."""

    def test_youtube_watch(self):
        from video_split.service.downloader import detect_platform
        p, vid = detect_platform("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert p == "youtube"
        assert vid == "dQw4w9WgXcQ"

    def test_youtube_shorts(self):
        from video_split.service.downloader import detect_platform
        p, vid = detect_platform("https://www.youtube.com/shorts/E9-dkgVnVO0")
        assert p == "youtube"
        assert vid == "E9-dkgVnVO0"

    def test_youtube_live(self):
        from video_split.service.downloader import detect_platform
        p, vid = detect_platform("https://www.youtube.com/live/dQw4w9WgXcQ")
        assert p == "youtube"
        assert vid == "dQw4w9WgXcQ"

    def test_youtu_be(self):
        from video_split.service.downloader import detect_platform
        p, vid = detect_platform("https://youtu.be/dQw4w9WgXcQ")
        assert p == "youtube"
        assert vid == "dQw4w9WgXcQ"

    def test_bilibili_bv(self):
        from video_split.service.downloader import detect_platform
        p, vid = detect_platform("https://www.bilibili.com/video/BV1N7A9zHE6a")
        assert p == "bilibili"
        assert vid == "BV1N7A9zHE6a"

    def test_bilibili_b23(self):
        from video_split.service.downloader import detect_platform
        p, vid = detect_platform("https://b23.tv/abc123")
        assert p == "bilibili"
        assert vid == "abc123"

    def test_xiaoyuzhou(self):
        from video_split.service.downloader import detect_platform
        p, vid = detect_platform("https://www.xiaoyuzhoufm.com/episode/69bbb17a3c625cc5ae1cf27a")
        assert p == "xiaoyuzhou"
        assert vid == "69bbb17a3c625cc5ae1cf27a"

    def test_xiaoyuzhou_no_www(self):
        from video_split.service.downloader import detect_platform
        p, vid = detect_platform("https://xiaoyuzhoufm.com/episode/69bbb17a3c625cc5ae1cf27a")
        assert p == "xiaoyuzhou"
        assert vid == "69bbb17a3c625cc5ae1cf27a"

    def test_unknown_url(self):
        from video_split.service.downloader import detect_platform
        p, vid = detect_platform("https://example.com/video/123")
        assert p == "unknown"
        assert vid == ""

    def test_platforms_are_isolated(self):
        """Cross-platform URLs must never match the wrong platform."""
        from video_split.service.downloader import detect_platform
        urls = {
            "youtube": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "bilibili": "https://www.bilibili.com/video/BV1N7A9zHE6a",
            "xiaoyuzhou": "https://www.xiaoyuzhoufm.com/episode/69bbb17a3c625cc5ae1cf27a",
        }
        for expected_platform, url in urls.items():
            p, _ = detect_platform(url)
            assert p == expected_platform, f"{url} detected as {p}, expected {expected_platform}"


# ---------------------------------------------------------------------------
# LLM retry tests (mock-based, no real API calls)
# ---------------------------------------------------------------------------

class TestLLMRetry:
    """Verify that analyze_transcript retries on transient errors."""

    @pytest.mark.asyncio
    async def test_retry_on_read_timeout(self):
        """LLM call retries on ReadTimeout and succeeds on 2nd attempt."""
        import httpx
        from unittest.mock import AsyncMock, patch, MagicMock

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": json.dumps({
                "summary": "test summary",
                "summary_en": "test summary en",
                "segments": [{"index": 0, "title": "Part 1", "title_en": "Part 1 en",
                              "summary": "s", "summary_en": "s en",
                              "start_seconds": 0, "end_seconds": 120}],
            })}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }

        call_count = 0
        original_post = httpx.AsyncClient.post

        async def mock_post(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ReadTimeout("Read timed out")
            return mock_response

        entries = [SubtitleEntry(start=0.0, duration=5.0, text="Hello world")]

        with patch.object(httpx.AsyncClient, "post", mock_post):
            result = await analyze_transcript(entries, 120)

        assert call_count == 2
        assert result.summary == "test summary"
        assert len(result.segments) == 1

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises(self):
        """LLM call raises after max retries exhausted."""
        import httpx
        from unittest.mock import patch

        call_count = 0

        async def mock_post(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise httpx.ReadTimeout("Read timed out")

        entries = [SubtitleEntry(start=0.0, duration=5.0, text="Hello world")]

        with patch.object(httpx.AsyncClient, "post", mock_post):
            with pytest.raises(httpx.ReadTimeout):
                await analyze_transcript(entries, 120)

        assert call_count == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_client_error(self):
        """4xx errors should not be retried."""
        import httpx
        from unittest.mock import patch, MagicMock

        call_count = 0

        async def mock_post(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            response = MagicMock()
            response.status_code = 400
            response.text = "Bad request"
            response.json.return_value = {"error": "bad request"}
            raise httpx.HTTPStatusError("400", request=MagicMock(), response=response)

        entries = [SubtitleEntry(start=0.0, duration=5.0, text="Hello world")]

        with patch.object(httpx.AsyncClient, "post", mock_post):
            with pytest.raises(httpx.HTTPStatusError):
                await analyze_transcript(entries, 120)

        assert call_count == 1


# ---------------------------------------------------------------------------
# Retryable status tests
# ---------------------------------------------------------------------------

class TestRetryableStatuses:

    def test_failed_download_is_retryable(self):
        from video_split.service.task_manager import RETRYABLE_STATUSES
        assert "failed_download" in RETRYABLE_STATUSES

    def test_failed_transcribe_is_retryable(self):
        from video_split.service.task_manager import RETRYABLE_STATUSES
        assert "failed_transcribe" in RETRYABLE_STATUSES

    def test_failed_analyze_is_retryable(self):
        from video_split.service.task_manager import RETRYABLE_STATUSES
        assert "failed_analyze" in RETRYABLE_STATUSES

    def test_completed_is_not_retryable(self):
        from video_split.service.task_manager import RETRYABLE_STATUSES
        assert "completed" not in RETRYABLE_STATUSES

    def test_cancelled_is_not_retryable(self):
        from video_split.service.task_manager import RETRYABLE_STATUSES
        assert "cancelled" not in RETRYABLE_STATUSES
