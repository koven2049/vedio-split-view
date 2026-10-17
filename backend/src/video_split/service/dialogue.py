"""Dialogue-formatting service: raw subtitle stream → speaker-labeled paragraphs.

Mirrors the layout of a WeChat official-account article: a talking-head /
interview transcript becomes ``说话人名： 内容`` paragraphs with a timestamp
only at the start of each speaking turn, single-speaker content becomes plain
sectioned paragraphs.

Anti-hallucination design: the LLM only decides *who* speaks over *which
subtitle index range* — it never rewrites text. Paragraph bodies are rendered
deterministically from ``subtitles[f..t]`` by this module, so no words can be
invented or dropped.
"""

from __future__ import annotations

import json
import logging
import re
import time as _time

import httpx

from video_split.config import get_settings
from video_split.service.downloader import SubtitleEntry
from video_split.service.llm_http import post_chat

logger = logging.getLogger(__name__)

_ASCII_ALNUM = re.compile(r"[A-Za-z0-9]")
_SENTENCE_END = tuple("。？！?!…")
# Safety valve for fallback paragraphs: subtitle runs without any sentence
# punctuation (e.g. auto-generated CJK captions) would otherwise fuse into
# one giant block.
_FALLBACK_MAX_LINES = 40


def _join_texts(texts: list[str]) -> str:
    """Deterministically render paragraph text from subtitle fragments.

    Whitespace runs inside a fragment collapse to one space; two fragments
    are joined with a single space only when both boundary characters are
    ASCII alphanumerics (Latin words must not fuse), otherwise concatenated
    directly (CJK needs no space).
    """
    out = ""
    for raw in texts:
        part = re.sub(r"\s+", " ", (raw or "").strip())
        if not part:
            continue
        if out and _ASCII_ALNUM.search(out[-1]) and _ASCII_ALNUM.search(part[0]):
            out += " " + part
        else:
            out += part
    return out


def _build_numbered_transcript(subtitles: list[SubtitleEntry]) -> str:
    """Render subtitles as ``[i]|mm:ss|text`` lines (i is the 0-based index)."""
    lines: list[str] = []
    for i, entry in enumerate(subtitles):
        minutes = int(entry.start) // 60
        seconds = int(entry.start) % 60
        lines.append(f"[{i}]|{minutes:02d}:{seconds:02d}|{entry.text}")
    return "\n".join(lines)


def _build_prompt(
    numbered: str,
    *,
    platform: str,
    uploader: str,
    title: str,
    duration_seconds: int,
    total_lines: int,
) -> str:
    duration_minutes = duration_seconds / 60
    return f"""你是一位专业的访谈/播客逐字稿整理编辑。请把下面带编号的字幕整理成「说话人-自然段」结构的对话稿。

## 背景
- 平台：{platform}
- 上传者/频道：{uploader or "未知"}
- 标题：{title or "未知"}
- 时长：{duration_seconds}秒（约{duration_minutes:.0f}分钟），共{total_lines}行字幕（下标 0 到 {total_lines - 1}）

## 任务：说话人识别与分段
1. 为每行字幕判断说话人，并把全部字幕切分成连续的 blocks（自然段）：
   - blocks 必须按顺序**无缝覆盖全部下标 0..{total_lines - 1}**：不重叠、不跳过、首尾相接
   - 每个 block 是一个自然段：同一说话人按话题/停顿切段，典型包含 2~10 行字幕
   - 说话人变化必须开新 block
2. 说话人识别规则：
   - 从称呼、自我介绍、问答模式等线索推断；能确定真名时用真名（知名人物用通行中文译名）
   - 不能确定时用 主持人 / 嘉宾1 / 嘉宾2 这类角色称呼
   - 单人内容（独白、教程、单人播报）统一只给一个说话人
3. sections：只在内容出现重大话题切换处给 3~8 个小节标题（可选，无明显切换时给空数组）

## 防幻觉约束（最重要）
- 你只输出说话人和字幕下标区间，绝对不要改写、翻译或复述字幕文本
- f/t 必须是输入里出现过的下标，不能虚构；正文由系统按区间从原字幕拼接

返回严格合法的 JSON，结构如下：
{{
  "speakers": [{{"id": "A", "name": "主持人杰森", "role": "主持人"}}],
  "blocks": [{{"s": "A", "f": 0, "t": 24}}],
  "sections": [{{"i": 3, "title": "开场"}}]
}}

字幕内容：
{numbered}"""


def _parse_llm_response(text: str) -> dict:
    """Extract the JSON payload, tolerating malformed quotes via json_repair."""
    json_match = re.search(r"\{[\s\S]*\}", text)
    if not json_match:
        raise ValueError("dialogue LLM response does not contain JSON")
    raw = json_match.group()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        from json_repair import repair_json

        data = json.loads(repair_json(raw, return_objects=False))
        logger.warning("[dialogue] response JSON was malformed; repaired %d chars", len(raw))
    if not isinstance(data, dict):
        raise TypeError("dialogue LLM response is not a JSON object")
    return data


def _validate_blocks(data: dict, total_lines: int) -> tuple[list[dict], list[dict], list[dict]]:
    """Validate + normalize speakers/blocks/sections; raise ValueError if broken."""
    speakers: list[dict] = []
    speaker_ids: set[str] = set()
    for sp in data.get("speakers") or []:
        if not isinstance(sp, dict):
            continue
        sid = str(sp.get("id", "")).strip()
        if not sid or sid in speaker_ids:
            continue
        speaker_ids.add(sid)
        speakers.append(
            {
                "id": sid,
                "name": str(sp.get("name", "")).strip() or sid,
                "role": str(sp.get("role", "")).strip(),
            }
        )
    if not speakers:
        raise ValueError("no speakers in dialogue response")

    raw_blocks = data.get("blocks") or []
    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise ValueError("no blocks in dialogue response")
    blocks: list[dict] = []
    expected = 0
    for b in raw_blocks:
        if not isinstance(b, dict):
            raise TypeError("malformed block entry")
        try:
            f = int(b.get("f"))
            t = int(b.get("t"))
        except (TypeError, ValueError):
            raise ValueError("block f/t not integers")
        sid = str(b.get("s", "")).strip()
        if sid not in speaker_ids:
            raise ValueError(f"unknown speaker id {sid!r}")
        if f != expected or t < f or t >= total_lines:
            raise ValueError(
                f"blocks must cover 0..{total_lines - 1} in order without "
                f"gaps/overlap (expected f={expected}, got f={f}, t={t})"
            )
        blocks.append({"s": sid, "f": f, "t": t})
        expected = t + 1
    if expected != total_lines:
        raise ValueError(f"blocks stop at {expected - 1}, need {total_lines - 1}")

    # Sections are advisory: drop invalid entries instead of discarding the
    # whole (valid, expensive) block layout.
    sections: list[dict] = []
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        try:
            i = int(sec.get("i"))
        except (TypeError, ValueError):
            continue
        title = str(sec.get("title", "")).strip()
        if 0 <= i < len(blocks) and title:
            sections.append({"i": i, "title": title})
    return speakers, blocks, sections


def _fallback_dialogue(subtitles: list[SubtitleEntry]) -> dict:
    """Deterministic single-speaker layout used when the LLM path fails.

    One speaker ("讲述者"); paragraphs accumulate subtitle lines until two
    sentence-final punctuation marks (。？！?!…) have been seen, then cut.
    Never raises.
    """
    blocks: list[dict] = []
    start = 0
    sentences = 0
    for i, entry in enumerate(subtitles):
        text = (entry.text or "").rstrip()
        if text.endswith(_SENTENCE_END):
            sentences += 1
            if sentences >= 2:
                blocks.append({"s": "A", "f": start, "t": i})
                start = i + 1
                sentences = 0
        if i - start + 1 >= _FALLBACK_MAX_LINES:
            blocks.append({"s": "A", "f": start, "t": i})
            start = i + 1
            sentences = 0
    if start < len(subtitles):
        blocks.append({"s": "A", "f": start, "t": len(subtitles) - 1})
    return {
        "speakers": [{"id": "A", "name": "讲述者", "role": ""}],
        "blocks": blocks,
        "sections": [],
        "fallback": True,
    }


async def _call_llm_dialogue(
    prompt: str,
    model: str,
    api_key: str,
    llm_base: str,
    timeout_ms: int,
    max_tokens: int,
) -> dict:
    url = f"{llm_base.rstrip('/')}/chat/completions"
    payload: dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    if "glm" in model.lower():
        payload["thinking"] = {"type": "disabled"}
    return await post_chat(
        url,
        payload,
        {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        httpx.Timeout(timeout_ms / 1000),
    )


async def format_dialogue(
    subtitles: list[SubtitleEntry],
    *,
    platform: str,
    uploader: str,
    title: str,
    duration_seconds: int,
) -> tuple[dict, dict]:
    """Format a subtitle stream into the stored dialogue structure.

    Returns ``(dialogue, usage)`` where dialogue is ``{"speakers": [...],
    "blocks": [...], "sections": [...], "fallback": bool}`` with blocks
    carrying subtitle index ranges (``f``..``t`` inclusive) — never rendered
    text — and usage is ``{"llm_model", "llm_prompt_tokens",
    "llm_completion_tokens", "llm_total_tokens"}`` for caller-side cost
    accounting (all zeros on the fallback path). Any LLM/validation failure
    degrades to the deterministic single-speaker fallback; this function
    never raises.
    """
    n = len(subtitles)
    if n == 0:
        return {"speakers": [], "blocks": [], "sections": [], "fallback": True}, {}

    settings = get_settings()
    prompt = _build_prompt(
        _build_numbered_transcript(subtitles),
        platform=platform,
        uploader=uploader,
        title=title,
        duration_seconds=duration_seconds,
        total_lines=n,
    )

    t0 = _time.monotonic()
    provider = "primary"
    try:
        result = await _call_llm_dialogue(
            prompt,
            settings.llm.model,
            settings.llm.api_key,
            settings.llm.base_url,
            settings.llm.timeout_ms,
            settings.llm.max_tokens,
        )
    except Exception as primary_err:  # noqa: BLE001 — any failure must fall back
        if not settings.llm_backup.enabled:
            await _log_dialogue_call("primary", settings.llm.model, "error", str(primary_err), t0)
            return _fallback_dialogue(subtitles), {}
        logger.warning(
            "[dialogue] primary failed (%s), switching to backup (%s)",
            str(primary_err)[:80],
            settings.llm_backup.model,
        )
        provider = "backup"
        try:
            result = await _call_llm_dialogue(
                prompt,
                settings.llm_backup.model,
                settings.llm_backup.api_key,
                settings.llm_backup.base_url,
                settings.llm_backup.timeout_ms,
                settings.llm_backup.max_tokens,
            )
        except Exception as backup_err:  # noqa: BLE001 — any failure must fall back
            await _log_dialogue_call(
                "backup", settings.llm_backup.model, "error", str(backup_err), t0
            )
            return _fallback_dialogue(subtitles), {}

    usage: dict = {}
    llm_model = settings.llm.model if provider == "primary" else settings.llm_backup.model
    try:
        # A gateway can answer 200 with a body that has no usable "choices"
        # shape (wrapped error, content filter) — extraction must stay inside
        # the guard so format_dialogue keeps its never-raises contract.
        content = result["choices"][0]["message"]["content"]
        usage = result.get("usage") or {}
        speakers, blocks, sections = _validate_blocks(_parse_llm_response(content), n)
    except Exception as parse_err:  # noqa: BLE001 — any failure must fall back
        logger.warning("[dialogue] LLM output invalid (%s), falling back", str(parse_err)[:120])
        await _log_dialogue_call(
            provider,
            llm_model,
            "error",
            f"invalid output: {parse_err}",
            t0,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        )
        return _fallback_dialogue(subtitles), {}

    usage_payload = {
        "llm_model": llm_model,
        "llm_prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "llm_completion_tokens": int(usage.get("completion_tokens") or 0),
        "llm_total_tokens": int(usage.get("total_tokens") or 0),
    }
    await _log_dialogue_call(
        provider,
        llm_model,
        "ok",
        "",
        t0,
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        total_tokens=usage.get("total_tokens", 0),
    )
    logger.info(
        "[dialogue] formatted %d lines into %d blocks, %d speakers", n, len(blocks), len(speakers)
    )
    return (
        {"speakers": speakers, "blocks": blocks, "sections": sections, "fallback": False},
        usage_payload,
    )


async def _log_dialogue_call(
    provider: str,
    model: str,
    status: str,
    error: str,
    t0: float,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    from video_split.service.analyzer import _log_llm_call

    await _log_llm_call(
        provider=provider,
        model=model,
        purpose="dialogue",
        status=status,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        duration_ms=int((_time.monotonic() - t0) * 1000),
        error_message=error,
    )


def derive_dialogue_view(stored: dict, subtitles: list[SubtitleEntry]) -> dict:
    """Render the stored dialogue structure into the API-facing view.

    Paragraph text and start times are re-derived from ``subtitles`` so the
    stored JSON stays index-only (no duplicated text to drift out of sync).
    Block ``index`` is the position in the stored blocks array, which is what
    ``sections[i]`` refers to.
    """
    speakers_raw = stored.get("speakers") or []
    name_by_id = {
        str(sp.get("id", "")): str(sp.get("name", ""))
        for sp in speakers_raw
        if isinstance(sp, dict)
    }
    n = len(subtitles)
    blocks_out: list[dict] = []
    prev_speaker: str | None = None
    for idx, b in enumerate(stored.get("blocks") or []):
        if not isinstance(b, dict):
            continue
        try:
            f = int(b.get("f", 0))
            t = int(b.get("t", 0))
        except (TypeError, ValueError):
            continue
        f, t = max(0, min(f, n - 1)), max(0, min(t, n - 1))
        if t < f:
            continue
        speaker_id = str(b.get("s", ""))
        blocks_out.append(
            {
                "index": idx,
                "speaker_id": speaker_id,
                "speaker": name_by_id.get(speaker_id) or speaker_id,
                "start": subtitles[f].start,
                "text": _join_texts([s.text for s in subtitles[f : t + 1]]),
                "new_turn": idx == 0 or speaker_id != prev_speaker,
            }
        )
        prev_speaker = speaker_id

    stored_blocks = stored.get("blocks") or []
    sections_out: list[dict] = []
    for sec in stored.get("sections") or []:
        if not isinstance(sec, dict) or not isinstance(sec.get("i"), int):
            continue
        title = str(sec.get("title", "")).strip()
        if 0 <= sec["i"] < len(stored_blocks) and title:
            sections_out.append({"index": sec["i"], "title": title})
    return {
        "speakers": [
            {
                "id": str(sp.get("id", "")),
                "name": str(sp.get("name", "")),
                "role": str(sp.get("role", "")),
            }
            for sp in speakers_raw
            if isinstance(sp, dict)
        ],
        "blocks": blocks_out,
        "sections": sections_out,
        "single_speaker": len(speakers_raw) == 1,
        "fallback": bool(stored.get("fallback", False)),
    }
