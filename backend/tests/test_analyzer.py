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

    def test_unescaped_inner_quotes_repaired(self):
        """glm-5.2 (reasoning model) routinely emits unescaped " inside JSON
        string values — e.g. summary_en: '... like the "trap theory" ...'.
        Strict json.loads fails; the parser must fall back to a tolerant
        repair so a 2-hour transcription isn't wasted on a stray quote."""
        raw = """```json
{
  "summary": "测试。",
  "segments": [
    {
      "index": 0,
      "title": "地缘博弈",
      "summary_en": "Western media cite the "trap theory" and "tribute system" narratives.",
      "start_seconds": 0,
      "end_seconds": 100
    }
  ]
}
```"""
        result = _parse_llm_response(raw)
        assert len(result.segments) == 1
        assert "trap theory" in result.segments[0].summary_en


class TestTranslatePrompt:
    """Per-line translation must not seal half-sentences with a full stop —
    the dialogue view joins lines into paragraphs, and a stray 。 at every
    ASR cut reads as "是聚焦于。寡头政治——" (video 62, 2026-11-09)."""

    def test_prompt_keeps_fragments_open(self):
        from video_split.service.analyzer import _build_translate_prompt

        prompt = _build_translate_prompt(["it's focused on", "oligarchy and corruption."])
        assert "[1] it's focused on" in prompt
        assert "[2] oligarchy and corruption." in prompt
        assert "半句" in prompt and "句号" in prompt

    def test_prompt_forbids_untranslated_english(self):
        from video_split.service.analyzer import _build_translate_prompt

        prompt = _build_translate_prompt(["emerging from the midterms"])
        assert "英文" in prompt
