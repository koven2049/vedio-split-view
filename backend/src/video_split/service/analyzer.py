from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

import httpx

from video_split.config import get_settings
from video_split.service.downloader import SubtitleEntry
from video_split.service.llm_http import post_chat

logger = logging.getLogger(__name__)


async def _log_llm_call(
    provider: str, model: str, purpose: str, status: str,
    prompt_tokens: int = 0, completion_tokens: int = 0,
    total_tokens: int = 0, duration_ms: int = 0,
    error_message: str = "", task_id: int | None = None,
) -> None:
    """Fire-and-forget LLM call record to DB."""
    try:
        from video_split.database import _get_session_factory
        from video_split.models import LLMLog
        factory = _get_session_factory()
        async with factory() as session:
            session.add(LLMLog(
                task_id=task_id, provider=provider, model=model,
                purpose=purpose, status=status, error_message=error_message[:500],
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                total_tokens=total_tokens, duration_ms=duration_ms,
            ))
            await session.commit()
    except Exception as e:
        logger.debug("[llm-log] failed to log: %s", e)


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
    essence: str = ""
    llm_usage: LLMUsage = field(default_factory=LLMUsage)


def _build_transcript_text(subtitles: list[SubtitleEntry]) -> str:
    lines: list[str] = []
    for entry in subtitles:
        minutes = int(entry.start) // 60
        seconds = int(entry.start) % 60
        lines.append(f"[{minutes:02d}:{seconds:02d}] {entry.text}")
    return "\n".join(lines)


def _build_prompt(transcript: str, duration_seconds: int, subtitle_chars: int, *, skip_segmentation: bool = False) -> str:
    duration_minutes = duration_seconds / 60
    essence_min = int(subtitle_chars * 0.05)
    essence_max = int(subtitle_chars * 0.10)

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

    return f"""你是一位视频内容分析师。请分析以下视频字幕，完成三项任务：

## 任务一：总结
用中文写一段100-150字的视频总结，涵盖视频的核心主题、关键论点和主要结论。

## 任务二：精华总结
将视频内容浓缩成一篇精华总结，长度在{essence_min}到{essence_max}字之间。

要求：
- 用中文以流畅的叙述体撰写，可直接作为独立文章阅读
- 不要使用对话形式（如"A说：…B说：…"），直接转述内容
- 不要包含时间信息
- 覆盖视频的核心主题、关键论点、重要细节和主要结论
- 保持逻辑连贯，段落自然过渡

## 任务三：主题分段
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
  "essence": "精华总结（叙述体，字幕量5-10%）",
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
    raw = json_match.group()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Reasoning models (glm-5.2 etc.) routinely emit unescaped " inside
        # string values — e.g. 'like the "trap theory"'. Fall back to a
        # tolerant repair so a multi-minute transcription isn't wasted on a
        # stray quote. Still raises if even repair can't salvage it.
        from json_repair import repair_json
        repaired = repair_json(raw, return_objects=False)
        data = json.loads(repaired)
        logger.warning("[llm] response JSON was malformed; repaired %d chars", len(raw))

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
        essence=data.get("essence", ""),
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
    prompt = _build_prompt(
        transcript_text, duration_seconds,
        subtitle_chars=sum(len(s.text) for s in subtitles),
        skip_segmentation=skip_seg,
    )

    import time as _time

    async def _call_llm_analysis(model: str, api_key: str, llm_base: str, timeout_ms: int, max_tokens: int) -> dict:
        url = f"{llm_base.rstrip('/')}/chat/completions"
        payload: dict[str, object] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        if "glm" in model.lower():
            payload["thinking"] = {"type": "disabled"}
        r = await post_chat(
            url, payload,
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            httpx.Timeout(timeout_ms / 1000),
        )
        return r

    t0 = _time.monotonic()
    result = None
    provider = "primary"
    try:
        result = await _call_llm_analysis(
            settings.llm.model, settings.llm.api_key,
            settings.llm.base_url, settings.llm.timeout_ms, settings.llm.max_tokens,
        )
    except Exception as primary_err:
        if settings.llm_backup.enabled:
            logger.warning("[llm] primary failed (%s), switching to backup (%s)",
                           str(primary_err)[:80], settings.llm_backup.model)
            provider = "backup"
            result = await _call_llm_analysis(
                settings.llm_backup.model, settings.llm_backup.api_key,
                settings.llm_backup.base_url, settings.llm_backup.timeout_ms,
                settings.llm_backup.max_tokens,
            )
        else:
            raise

    duration_ms = int((_time.monotonic() - t0) * 1000)
    content = result["choices"][0]["message"]["content"]
    usage = result.get("usage", {})
    llm_model = settings.llm.model if provider == "primary" else settings.llm_backup.model
    await _log_llm_call(
        provider=provider, model=llm_model, purpose="analysis", status="ok",
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        total_tokens=usage.get("total_tokens", 0),
        duration_ms=duration_ms,
    )
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


def _is_mostly_chinese(subtitles: list[SubtitleEntry]) -> bool:
    """Heuristic: are these subtitles already in Chinese?"""
    sample = " ".join(s.text for s in subtitles[:200])
    cjk = sum(1 for c in sample if "一" <= c <= "鿿" or "぀" <= c <= "ヿ" or "가" <= c <= "힯")
    if cjk < 20:
        return False  # predominantly Latin script
    zh = sum(1 for c in sample if "一" <= c <= "鿿")
    return zh / cjk > 0.6


async def translate_subtitles_to_chinese(
    subtitles: list[SubtitleEntry],
) -> list[SubtitleEntry]:
    """Translate non-Chinese subtitles to Chinese via the configured LLM.

    Returns originals unchanged if already Chinese or translation fails.
    Preserves all timing (start/duration); only ``text`` is replaced.
    """
    if not subtitles or _is_mostly_chinese(subtitles):
        return subtitles

    settings = get_settings()
    base_url = settings.llm.base_url.rstrip("/")
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.llm.api_key}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(settings.llm.timeout_ms / 1000)

    async def _translate_batch(texts: list[str]) -> list[str | None]:
        """Translate one batch; return list of translated-or-None strings."""
        numbered = "\n".join(f"[{j+1}] {t}" for j, t in enumerate(texts))
        prompt = (
            "请将以下视频字幕逐行翻译成简体中文。保持口语化风格。\n"
            "每行前有行号，请返回 JSON 对象，key 为行号，value 为中文翻译。\n"
            "如果两行原文需要合并成一句翻译，请将它们拆回两行，每行独立翻译。\n\n"
            + numbered
        )

        async def _call_llm(model: str, api_key: str, llm_base: str, timeout_ms: int, max_tokens: int) -> str:
            payload: dict[str, object] = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.2,
            }
            if "glm" in model.lower():
                payload["thinking"] = {"type": "disabled"}
            # Retry on 429 (rate limit) with exponential backoff
            for attempt in range(4):
                try:
                    r = await post_chat(
                        f"{llm_base.rstrip('/')}/chat/completions",
                        payload,
                        {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        httpx.Timeout(timeout_ms / 1000),
                    )
                    return r["choices"][0]["message"]["content"]
                except Exception as e:
                    if "429" in str(e) and attempt < 3:
                        wait = 2 ** (attempt + 1)
                        logger.info("[translate] 429 rate limit, retry %d/3 in %ds", attempt + 1, wait)
                        await asyncio.sleep(wait)
                    else:
                        raise
            raise RuntimeError("unreachable")

        content = None
        t0 = asyncio.get_event_loop().time()
        provider = "primary"
        try:
            content = await _call_llm(
                settings.llm.model, settings.llm.api_key,
                settings.llm.base_url, settings.llm.timeout_ms, settings.llm.max_tokens,
            )
        except Exception as primary_err:
            if settings.llm_backup.enabled:
                logger.warning("[translate] primary failed (%s), switching to backup (%s)",
                               str(primary_err)[:60], settings.llm_backup.model)
                provider = "backup"
                content = await _call_llm(
                    settings.llm_backup.model, settings.llm_backup.api_key,
                    settings.llm_backup.base_url, settings.llm_backup.timeout_ms,
                    settings.llm_backup.max_tokens,
                )
            else:
                raise

        duration_ms = int((asyncio.get_event_loop().time() - t0) * 1000)
        llm_model = settings.llm.model if provider == "primary" else settings.llm_backup.model
        await _log_llm_call(
            provider=provider, model=llm_model, purpose="translation", status="ok",
            duration_ms=duration_ms,
        )

        # Parse JSON object { "1": "...", "2": "...", ... }
        json_match = re.search(r"\{[\s\S]*\}", content)
        if not json_match:
            raise ValueError("no JSON object in response")
        try:
            tr_map = json.loads(json_match.group())
        except json.JSONDecodeError:
            from json_repair import repair_json
            repaired = repair_json(json_match.group(), return_objects=True)
            tr_map = repaired if isinstance(repaired, dict) else json.loads(repaired)

        if not isinstance(tr_map, dict):
            raise ValueError("response is not a JSON object")

        out: list[str | None] = []
        for j in range(len(texts)):
            key = str(j + 1)
            val = tr_map.get(key)
            out.append(val if isinstance(val, str) and val.strip() else None)
        return out

    chunk_size = 50
    translated: list[str] = []

    for i in range(0, len(subtitles), chunk_size):
        if i > 0:
            await asyncio.sleep(1)  # throttle to avoid 429
        chunk_texts = [s.text for s in subtitles[i:i + chunk_size]]
        chunk_num = i // chunk_size + 1
        total_chunks = (len(subtitles) + chunk_size - 1) // chunk_size

        try:
            batch = await _translate_batch(chunk_texts)
            # Fill gaps from missing keys with original text
            for j, t in enumerate(batch):
                translated.append(t if t is not None else chunk_texts[j])
            missing = sum(1 for t in batch if t is None)
            if missing:
                logger.warning("[translate] chunk %d/%d: %d/%d keys missing",
                               chunk_num, total_chunks, missing, len(chunk_texts))
            else:
                logger.info("[translate] chunk %d/%d done (%d entries)", chunk_num, total_chunks, len(chunk_texts))
        except Exception as e:
            # Split failed chunk in half and retry each half
            logger.warning("[translate] chunk %d/%d failed (%s), splitting", chunk_num, total_chunks, e)
            mid = len(chunk_texts) // 2
            for half_start in (0, mid):
                half = chunk_texts[half_start:half_start + mid] if half_start + mid < len(chunk_texts) else chunk_texts[half_start:]
                if not half:
                    continue
                try:
                    batch = await _translate_batch(half)
                    for j, t in enumerate(batch):
                        translated.append(t if t is not None else half[j])
                except Exception as e2:
                    logger.warning("[translate] chunk %d half failed (%s), keeping original", chunk_num, e2)
                    translated.extend(half)

    if len(translated) != len(subtitles):
        logger.warning("[translate] count mismatch (%d vs %d), returning originals", len(translated), len(subtitles))
        return subtitles

    translated_count = sum(1 for t in translated if t not in [s.text for s in subtitles])
    logger.info("[translate] translated %d/%d entries to Chinese", translated_count, len(translated))
    return [
        SubtitleEntry(start=s.start, duration=s.duration, text=translated[i])
        for i, s in enumerate(subtitles)
    ]
