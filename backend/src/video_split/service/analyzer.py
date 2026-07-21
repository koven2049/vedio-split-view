from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import httpx

from video_split.config import get_settings
from video_split.service.downloader import SubtitleEntry
from video_split.service.llm_http import post_chat

logger = logging.getLogger(__name__)


@dataclass
class SegmentResult:
    index: int
    title: str
    summary: str
    start_seconds: int
    end_seconds: int
    title_en: str = ""
    summary_en: str = ""


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""


@dataclass
class AnalysisResult:
    summary: str
    segments: list[SegmentResult] = field(default_factory=list)
    summary_en: str = ""
    llm_usage: LLMUsage = field(default_factory=LLMUsage)


def _build_transcript_text(subtitles: list[SubtitleEntry]) -> str:
    lines: list[str] = []
    for entry in subtitles:
        minutes = int(entry.start) // 60
        seconds = int(entry.start) % 60
        lines.append(f"[{minutes:02d}:{seconds:02d}] {entry.text}")
    return "\n".join(lines)


def _build_prompt(transcript: str, duration_seconds: int, *, skip_segmentation: bool = False) -> str:
    duration_minutes = duration_seconds / 60

    if skip_segmentation:
        return f"""你是一位视频内容分析师。请分析以下视频字幕（视频时长{duration_seconds}秒）。

## 任务：总结
用中文写一段100-150字的视频总结，涵盖视频的核心主题、关键论点和主要结论。同时提供英文版本。

由于视频较短，无需分段，segments 只需包含一个覆盖全程的段落。

返回严格合法的 JSON，结构如下：
{{
  "summary": "中文总结（100-150字）",
  "summary_en": "English summary",
  "segments": [
    {{
      "index": 0,
      "title": "中文标题",
      "title_en": "English title",
      "summary": "中文摘要",
      "summary_en": "English summary",
      "start_seconds": 0,
      "end_seconds": {duration_seconds}
    }}
  ]
}}

字幕内容：
{transcript}"""

    return f"""你是一位视频内容分析师。请分析以下视频字幕，完成两项任务：

## 任务一：总结
用中文写一段100-150字的视频总结，涵盖视频的核心主题、关键论点和主要结论。

## 任务二：主题分段
将视频按**内容主题的自然转换点**划分为若干段落。

关键原则：
- 根据话题/主题的实际切换来划分，而不是按固定时间间隔
- 每段应聚焦一个独立的主题或论点，让观众能快速跳到感兴趣的部分
- 段落长度应随内容自然变化：有的主题可能只讲了1分钟，有的可能讲了5分钟
- 段落必须覆盖完整视频时长（{duration_seconds}秒，约{duration_minutes:.0f}分钟），首尾相连、不重叠
- 每段需要一个清晰的标题和1-2句摘要
- 同时提供中英文版本

请仔细阅读字幕中话题切换的位置，精准标记 start_seconds / end_seconds。

返回严格合法的 JSON，结构如下：
{{
  "summary": "中文总结（100-150字）",
  "summary_en": "English summary",
  "segments": [
    {{
      "index": 0,
      "title": "中文标题",
      "title_en": "English title",
      "summary": "中文摘要",
      "summary_en": "English summary of this segment",
      "start_seconds": 0,
      "end_seconds": 125
    }}
  ]
}}

字幕内容：
{transcript}"""


def _parse_llm_response(text: str) -> AnalysisResult:
    json_match = re.search(r"\{[\s\S]*\}", text)
    if not json_match:
        raise ValueError("LLM response does not contain valid JSON")
    data = json.loads(json_match.group())

    segments = []
    for seg in data.get("segments", []):
        segments.append(
            SegmentResult(
                index=seg.get("index", 0),
                title=seg.get("title", ""),
                summary=seg.get("summary", ""),
                start_seconds=int(seg.get("start_seconds", 0)),
                end_seconds=int(seg.get("end_seconds", 0)),
                title_en=seg.get("title_en", ""),
                summary_en=seg.get("summary_en", ""),
            )
        )
    return AnalysisResult(
        summary=data.get("summary", ""),
        summary_en=data.get("summary_en", ""),
        segments=segments,
    )


async def analyze_transcript(
    subtitles: list[SubtitleEntry],
    duration_seconds: int,
) -> AnalysisResult:
    settings = get_settings()
    transcript_text = _build_transcript_text(subtitles)
    skip_seg = duration_seconds < settings.video.min_segment_duration_seconds
    if skip_seg:
        logger.info("[llm] Video too short (%ds < %ds), skipping segmentation",
                     duration_seconds, settings.video.min_segment_duration_seconds)
    prompt = _build_prompt(transcript_text, duration_seconds, skip_segmentation=skip_seg)

    base_url = settings.llm.base_url.rstrip("/")
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.llm.api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, object] = {
        "model": settings.llm.model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": settings.llm.max_tokens,
        "temperature": 0.3,
    }

    if "glm" in settings.llm.model.lower():
        payload["thinking"] = {"type": "disabled"}

    timeout = httpx.Timeout(settings.llm.timeout_ms / 1000)

    logger.info(
        "[llm] Calling %s model=%s subtitles=%d prompt_len=%d",
        base_url, settings.llm.model, len(subtitles), len(prompt),
    )

    result = await post_chat(url, payload, headers, timeout)

    content = result["choices"][0]["message"]["content"]
    usage = result.get("usage", {})
    logger.info(
        "[llm] Response OK — tokens: prompt=%s completion=%s total=%s",
        usage.get("prompt_tokens", "?"),
        usage.get("completion_tokens", "?"),
        usage.get("total_tokens", "?"),
    )
    parsed = _parse_llm_response(content)
    parsed.llm_usage = LLMUsage(
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        total_tokens=usage.get("total_tokens", 0),
        model=settings.llm.model,
    )
    return parsed
