from __future__ import annotations

import pytest

from video_split.service.analyzer import _build_transcript_text, _parse_llm_response
from video_split.service.downloader import SubtitleEntry


class TestBuildTranscriptText:
    def test_basic(self):
        subs = [
            SubtitleEntry(start=0.0, duration=5.0, text="Hello world"),
            SubtitleEntry(start=65.5, duration=3.0, text="Second line"),
            SubtitleEntry(start=3661.0, duration=2.0, text="One hour mark"),
        ]
        result = _build_transcript_text(subs)
        lines = result.split("\n")
        assert lines[0] == "[00:00] Hello world"
        assert lines[1] == "[01:05] Second line"
        assert lines[2] == "[61:01] One hour mark"

    def test_empty(self):
        result = _build_transcript_text([])
        assert result == ""


class TestParseLLMResponse:
    def test_valid_json(self):
        raw = """Here is the analysis:
```json
{
  "summary": "This video covers Python basics.",
  "segments": [
    {
      "index": 0,
      "title": "Introduction",
      "summary": "Overview of the course",
      "start_seconds": 0,
      "end_seconds": 180
    },
    {
      "index": 1,
      "title": "Variables",
      "summary": "Python variable types",
      "start_seconds": 180,
      "end_seconds": 420
    }
  ]
}
```"""
        result = _parse_llm_response(raw)
        assert result.summary == "This video covers Python basics."
        assert len(result.segments) == 2
        assert result.segments[0].title == "Introduction"
        assert result.segments[0].start_seconds == 0
        assert result.segments[0].end_seconds == 180
        assert result.segments[1].title == "Variables"

    def test_json_without_markdown(self):
        raw = '{"summary": "Test", "segments": []}'
        result = _parse_llm_response(raw)
        assert result.summary == "Test"
        assert result.segments == []

    def test_no_json(self):
        with pytest.raises(ValueError, match="does not contain valid JSON"):
            _parse_llm_response("This is just text without JSON")
