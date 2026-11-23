"""Feishu X links: collect in vedio-favorate, analyze only tweets with video."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from video_split.config import set_config_path
from video_split.service.downloader import VideoMeta, XPlatformError
from video_split.service.feishu import handle_message_event, reset_feishu_runtime_state

APP_SECRET = "super-secret-app"
VERIFICATION = "ver-token-xyz"
ENCRYPT_KEY = "enc-key-xyz"
ALLOWED_OPEN_ID = "ou_allowed"

X_STATUS_ID = "2124465845284008268"
X_LINK = f"https://x.com/rchen1386/status/{X_STATUS_ID}"
YOUTUBE_LINK = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
def feishu_env(test_config_path):
    path = Path(test_config_path)
    original = path.read_text()
    path.write_text(
        original
        + """
feishu:
  enabled: true
  result_base_url: "https://notes.example"
  allowed_open_ids:
    - ou_allowed
  secrets_file: feishu.yaml
"""
    )
    secrets = path.parent / "feishu.yaml"
    secrets.write_text(
        "app_id: cli_test\n"
        f"app_secret: {APP_SECRET}\n"
        f"verification_token: {VERIFICATION}\n"
        f"encrypt_key: {ENCRYPT_KEY}\n"
    )
    set_config_path(str(path))
    reset_feishu_runtime_state()
    yield {"secrets": secrets}
    path.write_text(original)
    set_config_path(str(path))
    reset_feishu_runtime_state()
    if secrets.exists():
        secrets.unlink()
    kek = path.parent / "secret.key"
    if kek.exists():
        kek.unlink()


def _enable_vfav(env) -> None:
    secrets = env["secrets"]
    secrets.write_text(
        secrets.read_text() + '\nvfav_base_url: "http://vfav.example"\nvfav_token: vft-token\n'
    )
    from video_split.service.feishu import load_vfav_forward

    load_vfav_forward.cache_clear()


def _payload(text: str, message_id: str = "om_x_1") -> dict:
    return {
        "schema": "2.0",
        "header": {"event_type": "im.message.receive_v1", "token": VERIFICATION},
        "event": {
            "sender": {
                "sender_id": {"open_id": ALLOWED_OPEN_ID},
                "sender_type": "user",
            },
            "message": {
                "message_id": message_id,
                "chat_id": "oc_1",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": text}),
            },
        },
    }


def _patch_replies(monkeypatch) -> list[dict]:
    replies: list[dict] = []

    async def fake_reply(_creds, _mid, card):
        replies.append(card)

    monkeypatch.setattr("video_split.service.feishu.feishu_client.reply_card", fake_reply)
    return replies


def _patch_forward(monkeypatch) -> list[str]:
    forwarded: list[str] = []

    async def fake_forward(url):
        forwarded.append(url)
        return "已加入下载队列：@rchen1386"

    monkeypatch.setattr("video_split.service.feishu.forward_to_vfav", fake_forward)
    return forwarded


def _patch_platform(monkeypatch) -> None:
    """Pin detection to the merged behavior: X becomes an analyzable platform."""

    def fake_detect(url: str) -> tuple[str, str]:
        if "youtube.com" in url or "youtu.be" in url:
            return "youtube", "dQw4w9WgXcQ"
        if "/status/" in url:
            return "x", X_STATUS_ID
        return "unknown", ""

    monkeypatch.setattr("video_split.service.feishu.detect_platform", fake_detect)


def _patch_probe(monkeypatch, exc: Exception | None = None) -> list[str]:
    probed: list[str] = []

    async def fake_extract(url):
        probed.append(url)
        if exc is not None:
            raise exc
        return VideoMeta(
            url=url,
            platform="x",
            video_id=X_STATUS_ID,
            title="测试推文",
            duration_seconds=336,
            thumbnail_url="",
        )

    monkeypatch.setattr("video_split.service.feishu.extract_metadata", fake_extract)
    return probed


def _patch_start(monkeypatch) -> list[str]:
    started: list[str] = []

    async def fake_start(_db, _user, url):
        started.append(url)
        return {"task_id": 7, "platform": "x"}

    monkeypatch.setattr("video_split.api.analysis.start_analysis_for_user", fake_start)
    monkeypatch.setattr("video_split.service.feishu.follow_task", AsyncMock())
    return started


def _titles(replies: list[dict]) -> list[str]:
    return [card["header"]["title"]["content"] for card in replies]


def _body(card: dict) -> str:
    return card["elements"][0]["text"]["content"]


async def test_video_tweet_forwarded_and_analyzed(db_session, feishu_env, monkeypatch):
    reset_feishu_runtime_state()
    _enable_vfav(feishu_env)
    from video_split.service.auth_service import ensure_admin_user

    await ensure_admin_user(db_session)
    replies = _patch_replies(monkeypatch)
    forwarded = _patch_forward(monkeypatch)
    _patch_platform(monkeypatch)
    probed = _patch_probe(monkeypatch)
    started = _patch_start(monkeypatch)

    await handle_message_event(_payload(f"看这个 {X_LINK}"), db_session)
    await asyncio.sleep(0)

    assert forwarded == [X_LINK]
    assert probed == [X_LINK]
    assert started == [X_LINK]
    assert _titles(replies) == ["已存入视频收藏", "已开始分析"]


async def test_text_only_tweet_forwarded_without_analysis(db_session, feishu_env, monkeypatch):
    reset_feishu_runtime_state()
    _enable_vfav(feishu_env)
    replies = _patch_replies(monkeypatch)
    forwarded = _patch_forward(monkeypatch)
    _patch_platform(monkeypatch)
    exc = XPlatformError("No video could be found in this tweet", "x_no_video")
    probed = _patch_probe(monkeypatch, exc)
    started = _patch_start(monkeypatch)

    await handle_message_event(_payload(X_LINK), db_session)
    await asyncio.sleep(0)

    assert forwarded == [X_LINK]
    assert probed == [X_LINK]
    assert started == []
    assert _titles(replies) == ["已存入视频收藏"]


async def test_text_only_tweet_without_vfav_replies_no_video(db_session, feishu_env, monkeypatch):
    reset_feishu_runtime_state()
    replies = _patch_replies(monkeypatch)
    forwarded = _patch_forward(monkeypatch)
    _patch_platform(monkeypatch)
    exc = XPlatformError("No video could be found in this tweet", "x_no_video")
    _patch_probe(monkeypatch, exc)
    started = _patch_start(monkeypatch)

    await handle_message_event(_payload(X_LINK), db_session)
    await asyncio.sleep(0)

    assert forwarded == []
    assert started == []
    assert _titles(replies) == ["分析失败"]
    assert "该推文不包含视频" in _body(replies[0])


async def test_unavailable_tweet_replies_error_after_forward(db_session, feishu_env, monkeypatch):
    reset_feishu_runtime_state()
    _enable_vfav(feishu_env)
    replies = _patch_replies(monkeypatch)
    forwarded = _patch_forward(monkeypatch)
    _patch_platform(monkeypatch)
    exc = XPlatformError("tweet is unavailable", "x_unavailable")
    _patch_probe(monkeypatch, exc)
    started = _patch_start(monkeypatch)

    await handle_message_event(_payload(X_LINK), db_session)
    await asyncio.sleep(0)

    assert forwarded == [X_LINK]
    assert started == []
    assert _titles(replies) == ["已存入视频收藏", "分析失败"]
    assert "该推文不可访问" in _body(replies[1])


async def test_x_link_mixed_with_youtube_analyzes_youtube(db_session, feishu_env, monkeypatch):
    reset_feishu_runtime_state()
    _enable_vfav(feishu_env)
    from video_split.service.auth_service import ensure_admin_user

    await ensure_admin_user(db_session)
    replies = _patch_replies(monkeypatch)
    forwarded = _patch_forward(monkeypatch)
    _patch_platform(monkeypatch)
    probed = _patch_probe(monkeypatch)
    started = _patch_start(monkeypatch)

    await handle_message_event(_payload(f"{YOUTUBE_LINK} 还有 {X_LINK}"), db_session)
    await asyncio.sleep(0)

    assert forwarded == [X_LINK]
    assert probed == []
    assert started == [YOUTUBE_LINK]
    assert _titles(replies) == ["已存入视频收藏", "已开始分析"]
    assert YOUTUBE_LINK in _body(replies[1])


async def test_plain_youtube_link_unchanged(db_session, feishu_env, monkeypatch):
    reset_feishu_runtime_state()
    _enable_vfav(feishu_env)
    from video_split.service.auth_service import ensure_admin_user

    await ensure_admin_user(db_session)
    replies = _patch_replies(monkeypatch)
    forwarded = _patch_forward(monkeypatch)
    _patch_platform(monkeypatch)
    probed = _patch_probe(monkeypatch)
    started = _patch_start(monkeypatch)

    await handle_message_event(_payload(YOUTUBE_LINK), db_session)
    await asyncio.sleep(0)

    assert forwarded == []
    assert probed == []
    assert started == [YOUTUBE_LINK]
    assert _titles(replies) == ["已开始分析"]


async def test_video_tweet_without_vfav_is_analyzed(db_session, feishu_env, monkeypatch):
    reset_feishu_runtime_state()
    from video_split.service.auth_service import ensure_admin_user

    await ensure_admin_user(db_session)
    replies = _patch_replies(monkeypatch)
    forwarded = _patch_forward(monkeypatch)
    _patch_platform(monkeypatch)
    probed = _patch_probe(monkeypatch)
    started = _patch_start(monkeypatch)

    await handle_message_event(_payload(X_LINK), db_session)
    await asyncio.sleep(0)

    assert forwarded == []
    assert probed == [X_LINK]
    assert started == [X_LINK]
    assert _titles(replies) == ["已开始分析"]


async def test_probe_timeout_replies_error(db_session, feishu_env, monkeypatch):
    reset_feishu_runtime_state()
    monkeypatch.setattr("video_split.service.feishu._X_PROBE_TIMEOUT", 0.01)
    replies = _patch_replies(monkeypatch)
    _patch_forward(monkeypatch)
    _patch_platform(monkeypatch)

    async def slow_extract(url):
        await asyncio.sleep(60)

    monkeypatch.setattr("video_split.service.feishu.extract_metadata", slow_extract)
    started = _patch_start(monkeypatch)

    await handle_message_event(_payload(X_LINK), db_session)
    await asyncio.sleep(0)

    assert started == []
    assert _titles(replies) == ["分析失败"]
    assert "读取推文超时" in _body(replies[0])
