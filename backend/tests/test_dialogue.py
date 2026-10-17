"""Dialogue-formatting service and API tests.

Covers: LLM output parsing, validation-failure fallbacks (gap / overlap /
unknown speaker / LLM error), deterministic text-join spacing, the derived
API view (new_turn / single_speaker / sections), and the GET/POST endpoints
(404s, permissions, force, cached regeneration).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from tests.conftest import admin_create_user, get_admin_token
from video_split.models import User, Video
from video_split.service import dialogue as dialogue_mod
from video_split.service.dialogue import (
    _join_texts,
    derive_dialogue_view,
    format_dialogue,
)
from video_split.service.downloader import SubtitleEntry

_SPEAKERS = [
    {"id": "A", "name": "主持人杰森", "role": "主持人"},
    {"id": "B", "name": "嘉宾小李", "role": "嘉宾"},
]
# Valid layout over 10 lines: host 0-3, guest 4-7, host 8-9.
_VALID_PAYLOAD = {
    "speakers": _SPEAKERS,
    "blocks": [
        {"s": "A", "f": 0, "t": 3},
        {"s": "B", "f": 4, "t": 7},
        {"s": "A", "f": 8, "t": 9},
    ],
    "sections": [
        {"i": 0, "title": "开场"},
        {"i": 1, "title": "嘉宾分享"},
    ],
}


def _subs(texts: list[str], step: float = 6.0) -> list[SubtitleEntry]:
    return [SubtitleEntry(start=i * step, duration=5.0, text=t) for i, t in enumerate(texts)]


def _ten_subs() -> list[SubtitleEntry]:
    return _subs([f"第{i}句话的内容。" for i in range(10)])


def _llm_response(payload: dict) -> dict:
    return {
        "choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


@pytest.fixture
def _config(test_config_path):
    """Point get_settings at the generated test config (same as _llm_config)."""
    from video_split.config import set_config_path

    set_config_path(test_config_path)


def _mock_llm(monkeypatch, payload_or_exc, calls: list | None = None) -> None:
    async def _fake_post_chat(url, payload, headers, timeout):
        if calls is not None:
            calls.append(url)
        if isinstance(payload_or_exc, Exception):
            raise payload_or_exc
        return _llm_response(payload_or_exc)

    monkeypatch.setattr(dialogue_mod, "post_chat", _fake_post_chat)


# ─── format_dialogue: LLM path ───


@pytest.mark.asyncio
async def test_format_dialogue_llm_ok(monkeypatch, _config):
    _mock_llm(monkeypatch, _VALID_PAYLOAD)
    subs = _ten_subs()
    result, usage = await format_dialogue(
        subs,
        platform="xiaoyuzhou",
        uploader="某播客",
        title="第1期",
        duration_seconds=3600,
    )
    assert result["fallback"] is False
    assert result["speakers"] == _SPEAKERS
    assert result["blocks"] == _VALID_PAYLOAD["blocks"]
    assert result["sections"] == [{"i": 0, "title": "开场"}, {"i": 1, "title": "嘉宾分享"}]
    # LLM usage is surfaced for caller-side cost accounting (mock: 10/20/30).
    assert usage["llm_prompt_tokens"] == 10
    assert usage["llm_completion_tokens"] == 20
    assert usage["llm_total_tokens"] == 30
    assert usage["llm_model"]


@pytest.mark.asyncio
async def test_fallback_on_malformed_llm_body(monkeypatch, _config):
    """Gateway 200 with no usable "choices" shape must degrade, never raise."""

    async def _fake_post_chat(url, payload, headers, timeout):
        return {"error": "wrapped failure"}  # 200 body without choices

    monkeypatch.setattr(dialogue_mod, "post_chat", _fake_post_chat)
    result, usage = await format_dialogue(
        _ten_subs(), platform="youtube", uploader="u", title="t", duration_seconds=60
    )
    assert result["fallback"] is True
    assert usage == {}


@pytest.mark.asyncio
async def test_format_dialogue_empty_subtitles():
    result, usage = await format_dialogue(
        [],
        platform="youtube",
        uploader="u",
        title="t",
        duration_seconds=0,
    )
    assert result == {"speakers": [], "blocks": [], "sections": [], "fallback": True}
    assert usage == {}


# ─── format_dialogue: validation fallbacks ───


def _assert_fallback(result_and_usage: tuple[dict, dict], subs: list[SubtitleEntry]) -> None:
    result, usage = result_and_usage
    assert result["fallback"] is True
    assert usage == {}  # fallback path carries no LLM usage
    assert result["speakers"] == [{"id": "A", "name": "讲述者", "role": ""}]
    # Fallback blocks must still cover every index exactly once.
    covered = [i for b in result["blocks"] for i in range(b["f"], b["t"] + 1)]
    assert covered == list(range(len(subs)))


@pytest.mark.asyncio
async def test_fallback_on_index_gap(monkeypatch, _config):
    payload = {
        "speakers": _SPEAKERS,
        "blocks": [{"s": "A", "f": 0, "t": 3}, {"s": "A", "f": 5, "t": 9}],  # skips 4
        "sections": [],
    }
    _mock_llm(monkeypatch, payload)
    subs = _ten_subs()
    _assert_fallback(
        await format_dialogue(
            subs, platform="bilibili", uploader="u", title="t", duration_seconds=60
        ),
        subs,
    )


@pytest.mark.asyncio
async def test_fallback_on_overlap(monkeypatch, _config):
    payload = {
        "speakers": _SPEAKERS,
        "blocks": [{"s": "A", "f": 0, "t": 5}, {"s": "B", "f": 4, "t": 9}],  # overlaps
        "sections": [],
    }
    _mock_llm(monkeypatch, payload)
    subs = _ten_subs()
    _assert_fallback(
        await format_dialogue(
            subs, platform="bilibili", uploader="u", title="t", duration_seconds=60
        ),
        subs,
    )


@pytest.mark.asyncio
async def test_fallback_on_unknown_speaker(monkeypatch, _config):
    payload = {
        "speakers": _SPEAKERS,
        "blocks": [{"s": "C", "f": 0, "t": 9}],  # C not in speakers
        "sections": [],
    }
    _mock_llm(monkeypatch, payload)
    subs = _ten_subs()
    _assert_fallback(
        await format_dialogue(
            subs, platform="bilibili", uploader="u", title="t", duration_seconds=60
        ),
        subs,
    )


@pytest.mark.asyncio
async def test_fallback_on_llm_error(monkeypatch, _config):
    _mock_llm(monkeypatch, RuntimeError("boom"))
    subs = _ten_subs()
    _assert_fallback(
        await format_dialogue(
            subs, platform="youtube", uploader="u", title="t", duration_seconds=60
        ),
        subs,
    )


@pytest.mark.asyncio
async def test_fallback_cuts_paragraphs_by_punctuation(monkeypatch, _config):
    _mock_llm(monkeypatch, RuntimeError("boom"))
    # Two sentence-final marks per paragraph → block per 2 subtitle lines.
    subs = _subs(["第一句。", "第二句！", "第三句？", "第四句。", "第五句？", "没有标点"])
    result, _usage = await format_dialogue(
        subs,
        platform="youtube",
        uploader="u",
        title="t",
        duration_seconds=36,
    )
    blocks = result["blocks"]
    assert [(b["f"], b["t"]) for b in blocks] == [(0, 1), (2, 3), (4, 5)]


# ─── text joining ───


def test_join_texts_ascii_space_cjk_direct():
    assert _join_texts(["Hello", "world"]) == "Hello world"
    assert _join_texts(["你好", "AI"]) == "你好AI"  # CJK→ASCII: no space
    assert _join_texts(["使用", "GPT-4", "模型。"]) == "使用GPT-4模型。"
    assert _join_texts(["  多个   空格  ", "清理"]) == "多个 空格清理"
    assert _join_texts(["", "  ", "only"]) == "only"
    assert _join_texts([]) == ""


# ─── derive_dialogue_view ───


def test_derive_dialogue_view_multi_speaker():
    subs = _subs(
        [
            "你好。",
            "欢迎。",
            "谢谢。",
            "再见。",
            "客官好。",
            "幸会。",
            "好的。",
            "嗯。",
            "回到。",
            "结尾。",
        ]
    )
    view = derive_dialogue_view(_VALID_PAYLOAD, subs)
    assert view["fallback"] is False
    assert view["single_speaker"] is False
    assert [s["id"] for s in view["speakers"]] == ["A", "B"]
    assert [b["index"] for b in view["blocks"]] == [0, 1, 2]
    # new_turn: first block + every speaker change
    assert [b["new_turn"] for b in view["blocks"]] == [True, True, True]
    assert [b["speaker"] for b in view["blocks"]] == ["主持人杰森", "嘉宾小李", "主持人杰森"]
    # start comes from subtitles[f].start
    assert [b["start"] for b in view["blocks"]] == [0.0, 24.0, 48.0]
    # text is joined from subtitles f..t
    assert view["blocks"][0]["text"] == "你好。欢迎。谢谢。再见。"
    assert view["blocks"][2]["text"] == "回到。结尾。"
    # sections map i → index
    assert view["sections"] == [{"index": 0, "title": "开场"}, {"index": 1, "title": "嘉宾分享"}]


def test_derive_dialogue_view_single_speaker():
    stored = {
        "speakers": [{"id": "A", "name": "讲述者", "role": ""}],
        "blocks": [{"s": "A", "f": 0, "t": 1}, {"s": "A", "f": 2, "t": 3}],
        "sections": [],
        "fallback": True,
    }
    view = derive_dialogue_view(stored, _subs(["a。", "b。", "c。", "d。"]))
    assert view["single_speaker"] is True
    assert view["fallback"] is True
    # Same speaker continues → only the first block is a new turn
    assert [b["new_turn"] for b in view["blocks"]] == [True, False]


def test_derive_dialogue_view_tolerates_bad_entries():
    stored = {
        "speakers": [{"id": "A", "name": "甲"}],
        "blocks": [
            {"s": "A", "f": 0, "t": 1},
            {"s": "?", "f": "x", "t": 2},
            {"s": "A", "f": 2, "t": 3},
        ],
        "sections": [{"i": 99, "title": "越界"}, {"i": 0, "title": "有效"}, {"i": 1, "title": ""}],
        "fallback": False,
    }
    view = derive_dialogue_view(stored, _subs(["a", "b", "c", "d"]))
    assert len(view["blocks"]) == 2  # malformed middle entry skipped
    assert [b["index"] for b in view["blocks"]] == [0, 2]  # keeps array position
    assert view["sections"] == [{"index": 0, "title": "有效"}]


# ─── API endpoints ───


async def _admin_user(db) -> User:
    result = await db.execute(select(User).where(User.username == "admin"))
    return result.scalar_one()


async def _create_video_with_subs(
    db,
    user_id: int,
    subtitles: list[SubtitleEntry],
    *,
    dialogue: dict | None = None,
    takeaways: list | None = None,
    uploader: str = "测试播客",
) -> Video:
    video = Video(
        user_id=user_id,
        url="https://www.xiaoyuzhou.com/episode/1",
        platform="xiaoyuzhou",
        video_id="ep1",
        title="对话第1期",
        thumbnail_url="",
        duration_seconds=60,
        summary="s",
        uploader=uploader,
        raw_transcript="...",
        subtitle_json=json.dumps(
            [{"start": s.start, "duration": s.duration, "text": s.text} for s in subtitles],
            ensure_ascii=False,
        ),
        dialogue_json=json.dumps(dialogue, ensure_ascii=False) if dialogue else "",
        takeaways_json=json.dumps(takeaways, ensure_ascii=False) if takeaways else "",
    )
    db.add(video)
    await db.commit()
    await db.refresh(video)
    return video


def _valid_payload_for(subs: list[SubtitleEntry]) -> dict:
    last = len(subs) - 1
    payload = json.loads(json.dumps(_VALID_PAYLOAD))
    payload["blocks"] = [
        {"s": "A", "f": 0, "t": max(0, min(3, last))},
        {"s": "B", "f": 4, "t": max(4, min(7, last))},
        {"s": "A", "f": 8, "t": max(8, last)},
    ]
    return payload


@pytest.mark.asyncio
async def test_get_dialogue_404_when_not_generated(client, db_session):
    token = await get_admin_token(client)
    admin = await _admin_user(db_session)
    video = await _create_video_with_subs(db_session, admin.id, _ten_subs())

    resp = await client.get(
        f"/api/videos/{video.id}/dialogue", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404
    assert "dialogue not generated" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_get_dialogue_404_missing_video(client):
    token = await get_admin_token(client)
    resp = await client.get(
        "/api/videos/999999/dialogue", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_dialogue_generates_and_get_returns(client, db_session, monkeypatch):
    subs = _ten_subs()
    calls: list[str] = []
    _mock_llm(monkeypatch, _valid_payload_for(subs), calls)

    token = await get_admin_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    admin = await _admin_user(db_session)
    video = await _create_video_with_subs(db_session, admin.id, subs)

    resp = await client.post(f"/api/videos/{video.id}/dialogue", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["video_id"] == video.id
    assert data["fallback"] is False
    assert data["single_speaker"] is False
    assert [b["speaker"] for b in data["blocks"]] == ["主持人杰森", "嘉宾小李", "主持人杰森"]
    assert data["blocks"][0]["text"].endswith("第3句话的内容。")
    assert data["sections"][0] == {"index": 0, "title": "开场"}
    assert len(calls) == 1

    # Stored in DB and surfaced on the video detail.
    await db_session.refresh(video)
    assert video.dialogue_json
    # Dialogue tokens are folded into the video's usage accounting (mock: 10/20/30).
    usage_data = json.loads(video.usage_json)
    assert usage_data["llm_prompt_tokens"] == 10
    assert usage_data["llm_completion_tokens"] == 20
    assert usage_data["llm_total_tokens"] == 30
    assert usage_data["llm_model"]
    detail = await client.get(f"/api/videos/{video.id}", headers=headers)
    assert detail.json()["has_dialogue"] is True

    # GET now returns the same derived view without another LLM call.
    resp2 = await client.get(f"/api/videos/{video.id}/dialogue", headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["blocks"] == data["blocks"]

    # POST without force reuses the stored layout (no extra LLM call)...
    resp3 = await client.post(f"/api/videos/{video.id}/dialogue", headers=headers)
    assert resp3.status_code == 200
    assert len(calls) == 1
    # ...force=true regenerates.
    resp4 = await client.post(f"/api/videos/{video.id}/dialogue?force=true", headers=headers)
    assert resp4.status_code == 200
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_post_dialogue_llm_failure_returns_fallback(client, db_session, monkeypatch):
    _mock_llm(monkeypatch, RuntimeError("llm down"))
    token = await get_admin_token(client)
    admin = await _admin_user(db_session)
    video = await _create_video_with_subs(db_session, admin.id, _ten_subs())

    resp = await client.post(
        f"/api/videos/{video.id}/dialogue", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["fallback"] is True
    assert data["single_speaker"] is True
    assert data["speakers"][0]["name"] == "讲述者"


@pytest.mark.asyncio
async def test_post_dialogue_viewer_forbidden(client, db_session, monkeypatch):
    _mock_llm(monkeypatch, _VALID_PAYLOAD)
    viewer_token = await admin_create_user(client, "viewer_no_dialogue", role="viewer")
    admin = await _admin_user(db_session)
    video = await _create_video_with_subs(db_session, admin.id, _ten_subs())

    resp = await client.post(
        f"/api/videos/{video.id}/dialogue",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_post_dialogue_missing_video_404(client):
    token = await get_admin_token(client)
    resp = await client.post(
        "/api/videos/999999/dialogue", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_dialogue_no_subtitles_400(client, db_session):
    token = await get_admin_token(client)
    admin = await _admin_user(db_session)
    video = await _create_video_with_subs(db_session, admin.id, [])  # no subtitle_json

    resp = await client.post(
        f"/api/videos/{video.id}/dialogue", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 400
    assert "subtitles" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_video_detail_returns_takeaways_and_intro(client, db_session):
    token = await get_admin_token(client)
    admin = await _admin_user(db_session)
    takeaways = [{"title": "要点", "body": "说明", "highlight": "金句"}]
    video = await _create_video_with_subs(
        db_session,
        admin.id,
        _ten_subs(),
        takeaways=takeaways,
    )
    video.show_intro = "一期关于测试的节目。"
    await db_session.commit()

    resp = await client.get(f"/api/videos/{video.id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert json.loads(data["takeaways_json"]) == takeaways
    assert data["show_intro"] == "一期关于测试的节目。"
    assert data["uploader"] == "测试播客"
    assert data["has_dialogue"] is False
