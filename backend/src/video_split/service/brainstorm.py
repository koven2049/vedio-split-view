"""Two-stage LLM brainstorm / mindmap generator.

Stage 1: Rearrange existing segment summaries into thematic chapters with key points (bilingual).
Stage 2: Extract precise quotes from the original transcript for each chapter (bilingual).
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from video_split.config import get_settings
from video_split.models import Video
from video_split.service.video_service import accumulate_usage

logger = logging.getLogger(__name__)


def _duration_guidelines(duration_seconds: int) -> dict[str, int]:
    """Return chapter / point / quote count guidelines based on video duration."""
    if duration_seconds < 600:
        return {
            "chapters_min": 2,
            "chapters_max": 3,
            "points_min": 2,
            "points_max": 3,
            "quotes_total_min": 1,
            "quotes_total_max": 2,
        }
    if duration_seconds < 1800:
        return {
            "chapters_min": 3,
            "chapters_max": 5,
            "points_min": 3,
            "points_max": 4,
            "quotes_total_min": 2,
            "quotes_total_max": 3,
        }
    if duration_seconds <= 3600:
        return {
            "chapters_min": 4,
            "chapters_max": 6,
            "points_min": 3,
            "points_max": 5,
            "quotes_total_min": 3,
            "quotes_total_max": 5,
        }
    # > 3600s (about 1h+)
    return {
        "chapters_min": 5,
        "chapters_max": 8,
        "points_min": 3,
        "points_max": 5,
        "quotes_total_min": 4,
        "quotes_total_max": 6,
    }


def _build_stage1_prompt(title: str, duration_seconds: int, segments: list[dict]) -> str:
    g = _duration_guidelines(duration_seconds)
    segments_json = json.dumps(segments, ensure_ascii=False, indent=2)
    return f"""你是一位内容结构专家。下面是一支视频的标题、时长，以及已有的分段摘要（每段含时间范围）。
请根据**主题**重新组织这些内容，合并相近话题、拆出清晰脉络；质量优先于数量。

## 时长与规模指引（约 {duration_seconds} 秒）
- 章节数量：约 {g["chapters_min"]}–{g["chapters_max"]} 个主题章节
- 每章要点数：约 {g["points_min"]}–{g["points_max"]} 条
- 全片引用总数（留给下一阶段从原文摘引）：约 {g["quotes_total_min"]}–{g["quotes_total_max"]} 条级别即可（本阶段只写要点，不写原文引用）

## 视频标题
{title}

## 已有分段（JSON）
{segments_json}

## 输出要求
返回**严格合法**的 JSON，且仅包含以下结构（中英文双语字段必填）：
{{
  "chapters": [
    {{
      "title": "中文主题标题",
      "title_en": "English Theme Title",
      "summary": "一句话中文概述",
      "summary_en": "One-line English summary",
      "key_points": [
        {{"text": "中文要点", "text_en": "English point"}}
      ]
    }}
  ]
}}

注意：
- 不要编造视频中未出现的具体事实；可基于给定分段摘要归纳。
- key_points 应覆盖原分段信息中的核心信息，避免空洞套话。"""


def _build_stage2_prompt(chapters: list[dict], transcript_text: str) -> str:
    chapters_json = json.dumps(chapters, ensure_ascii=False, indent=2)
    return f"""你是一位编辑。下面「章节结构」来自对视频内容的归纳，以及「带时间标记的字幕/转写全文」。
请为**每个章节**从原文中找出 1–2 条最能支撑该章主题的原句引用，并给出英文翻译与大致时间（从原文行首的 [MM:SS] 读取；若无法确定可写最接近的时间）。

## 章节结构（JSON）
{chapters_json}

## 原文转写（含时间标记）
{transcript_text}

## 输出要求
返回**严格合法**的 JSON：
{{
  "chapter_quotes": [
    {{
      "chapter_index": 0,
      "quotes": [
        {{"text": "原文引用", "text_en": "English translation", "time_ref": "23:15"}}
      ]
    }}
  ]
}}

规则：
- chapter_index 与上面章节数组下标一致（从 0 开始）。
- 引用必须来自原文，尽量保持原句，不要改写后再当原文。
- 若某章难以找到合适引用，quotes 可为空数组。"""


def _parse_llm_json(content: str) -> dict:
    json_match = re.search(r"\{[\s\S]*\}", content)
    if not json_match:
        raise ValueError("LLM response does not contain valid JSON")
    return json.loads(json_match.group())


async def _call_llm(prompt: str) -> tuple[dict, dict]:
    settings = get_settings()
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
        "[mindmap] LLM request model=%s prompt_len=%d",
        settings.llm.model,
        len(prompt),
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        result = resp.json()

    content = result["choices"][0]["message"]["content"]
    usage = result.get("usage", {})
    parsed = _parse_llm_json(content)
    return parsed, usage


def _transcript_text_from_video(video: Video) -> str:
    raw = (video.raw_transcript or "").strip()
    if raw:
        return raw
    sj = (video.subtitle_json or "").strip()
    if not sj:
        return ""
    try:
        entries = json.loads(sj)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(entries, list):
        return ""
    lines: list[str] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        start = float(e.get("start", 0))
        minutes = int(start) // 60
        seconds = int(start) % 60
        text = e.get("text", "") or ""
        lines.append(f"[{minutes:02d}:{seconds:02d}] {text}")
    return "\n".join(lines)


def _merge_quotes_into_chapters(chapters: list[dict], chapter_quotes: list[dict]) -> None:
    by_index: dict[int, list[dict]] = {}
    for row in chapter_quotes:
        if not isinstance(row, dict):
            continue
        idx = int(row.get("chapter_index", -1))
        quotes = row.get("quotes") or []
        if isinstance(quotes, list):
            by_index[idx] = [q for q in quotes if isinstance(q, dict)]

    for i, ch in enumerate(chapters):
        ch["quotes"] = by_index.get(i, [])


def _usage_payload_from_api(usage: dict, model: str) -> dict:
    return {
        "llm_model": model,
        "llm_prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "llm_completion_tokens": int(usage.get("completion_tokens") or 0),
        "llm_total_tokens": int(usage.get("total_tokens") or 0),
    }


def _combine_usage(u1: dict, u2: dict) -> dict:
    return {
        "llm_model": u1.get("llm_model", u2.get("llm_model", "")),
        "llm_prompt_tokens": int(u1.get("llm_prompt_tokens", 0)) + int(u2.get("llm_prompt_tokens", 0)),
        "llm_completion_tokens": int(u1.get("llm_completion_tokens", 0))
        + int(u2.get("llm_completion_tokens", 0)),
        "llm_total_tokens": int(u1.get("llm_total_tokens", 0)) + int(u2.get("llm_total_tokens", 0)),
    }


async def generate_mindmap(
    video_id: int,
    db: AsyncSession,
    *,
    usage_user_id: int | None = None,
) -> AsyncGenerator[dict, None]:
    result = await db.execute(
        select(Video)
        .options(selectinload(Video.segments))
        .where(Video.id == video_id)
    )
    video = result.scalar_one_or_none()
    if video is None:
        yield {"stage": "error", "progress": 0, "message": "Video not found"}
        return

    bill_user_id = usage_user_id if usage_user_id is not None else video.user_id

    segs = sorted(video.segments, key=lambda s: s.segment_index)
    if not segs:
        yield {"stage": "error", "progress": 0, "message": "没有可用的分段摘要，请先生成视频分析。"}
        return

    yield {"stage": "generating", "progress": 10, "message": "分析主题结构..."}

    segment_payload = [
        {
            "title": s.title,
            "summary": s.summary,
            "title_en": s.title_en,
            "summary_en": s.summary_en,
            "start_seconds": s.start_seconds,
            "end_seconds": s.end_seconds,
        }
        for s in segs
    ]

    settings = get_settings()
    stage1_data: dict
    usage1: dict
    try:
        prompt1 = _build_stage1_prompt(video.title, video.duration_seconds, segment_payload)
        stage1_data, usage1 = await _call_llm(prompt1)
    except Exception as e:
        logger.exception("[mindmap] Stage 1 failed for video_id=%s", video_id)
        yield {"stage": "error", "progress": 0, "message": str(e)}
        return

    chapters = stage1_data.get("chapters") or []
    if not isinstance(chapters, list) or not chapters:
        yield {"stage": "error", "progress": 0, "message": "阶段一未返回有效章节结构。"}
        return

    chapters = [c for c in chapters if isinstance(c, dict)]
    usage_acc = _usage_payload_from_api(usage1, settings.llm.model)

    yield {"stage": "stage1_done", "progress": 60, "message": "提取关键引用..."}

    transcript_text = _transcript_text_from_video(video)
    quotes_note = ""
    if not transcript_text.strip():
        quotes_note = "无可用字幕或转写文本，已跳过原文引用提取。"
        logger.warning("[mindmap] No transcript for video_id=%s", video_id)
    else:
        try:
            prompt2 = _build_stage2_prompt(chapters, transcript_text)
            stage2_data, usage2 = await _call_llm(prompt2)
            usage_acc = _combine_usage(usage_acc, _usage_payload_from_api(usage2, settings.llm.model))
            cq = stage2_data.get("chapter_quotes") or []
            if isinstance(cq, list):
                _merge_quotes_into_chapters(chapters, cq)
            else:
                quotes_note = "阶段二返回格式异常，未合并引用。"
        except Exception as e:
            logger.exception("[mindmap] Stage 2 failed for video_id=%s", video_id)
            quotes_note = f"引用提取未完成：{e}"

    for ch in chapters:
        ch.setdefault("quotes", [])

    generated_at = datetime.now(timezone.utc).isoformat()
    mindmap_data: dict = {
        "chapters": chapters,
        "usage": usage_acc,
        "generated_at": generated_at,
    }
    if quotes_note:
        mindmap_data["quotes_note"] = quotes_note

    video.mindmap_json = json.dumps(mindmap_data, ensure_ascii=False)
    await db.commit()

    await accumulate_usage(db, bill_user_id, usage_acc)

    yield {"stage": "complete", "progress": 100, "data": mindmap_data}
