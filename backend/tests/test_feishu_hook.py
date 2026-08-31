from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml
from httpx import AsyncClient

from video_split.config import get_settings, set_config_path
from video_split.secure import is_encrypted
from video_split.service.feishu import (
    card_result,
    result_note_url,
    compute_signature,
    encrypt_event,
    extract_urls,
    extract_urls_from_message,
    follow_task,
    handle_card_action,
    handle_message_event,
    load_feishu_credentials,
    notify_complete_ok,
    pick_supported_url,
    remember_origin,
    reset_feishu_runtime_state,
)
from video_split.service.task_runner import RunningTask, runner

APP_SECRET = "super-secret-app"
VERIFICATION = "ver-token-xyz"
ENCRYPT_KEY = "enc-key-xyz"
ALLOWED_OPEN_ID = "ou_allowed"


@pytest.fixture
def enable_feishu(test_config_path):
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
    yield {"secrets": secrets, "config_dir": path.parent}
    path.write_text(original)
    set_config_path(str(path))
    reset_feishu_runtime_state()
    if secrets.exists():
        secrets.unlink()
    kek = path.parent / "secret.key"
    if kek.exists():
        kek.unlink()


def _signed(body: bytes, encrypt_key: str = ENCRYPT_KEY) -> dict[str, str]:
    ts, nonce = "1710000000", "n1"
    return {
        "X-Lark-Request-Timestamp": ts,
        "X-Lark-Request-Nonce": nonce,
        "X-Lark-Signature": compute_signature(ts, nonce, encrypt_key, body),
        "Content-Type": "application/json",
    }


def _message_payload(text: str, *, open_id: str = ALLOWED_OPEN_ID, message_id: str = "om_1") -> dict:
    return {
        "schema": "2.0",
        "header": {
            "event_type": "im.message.receive_v1",
            "token": VERIFICATION,
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": open_id},
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


async def test_disabled_hook_is_404(client: AsyncClient):
    resp = await client.post("/api/hooks/feishu", json={"type": "url_verification", "challenge": "x"})
    assert resp.status_code == 404
    assert APP_SECRET not in resp.text


async def test_challenge_handshake(client: AsyncClient, enable_feishu):
    inner = {"challenge": "echo-me", "token": VERIFICATION, "type": "url_verification"}
    body = json.dumps(inner).encode()
    resp = await client.post("/api/hooks/feishu", content=body, headers=_signed(body))
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "echo-me"}
    assert APP_SECRET not in resp.text
    assert ENCRYPT_KEY not in resp.text
    assert VERIFICATION not in resp.text


async def test_encrypted_challenge(client: AsyncClient, enable_feishu):
    inner = json.dumps({"challenge": "enc-hi", "token": VERIFICATION, "type": "url_verification"})
    body = json.dumps({"encrypt": encrypt_event(inner, ENCRYPT_KEY)}).encode()
    resp = await client.post("/api/hooks/feishu", content=body, headers=_signed(body))
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "enc-hi"}


async def test_unsigned_plaintext_challenge(client: AsyncClient, enable_feishu):
    resp = await client.post(
        "/api/hooks/feishu",
        json={"challenge": "plain-hi", "token": VERIFICATION, "type": "url_verification"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "plain-hi"}


async def test_unsigned_encrypted_challenge(client: AsyncClient, enable_feishu):
    inner = json.dumps({"challenge": "enc-unsigned", "token": VERIFICATION, "type": "url_verification"})
    body = json.dumps({"encrypt": encrypt_event(inner, ENCRYPT_KEY)}).encode()
    resp = await client.post("/api/hooks/feishu", content=body, headers={"Content-Type": "application/json"})
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "enc-unsigned"}


async def test_invalid_signature(client: AsyncClient, enable_feishu):
    body = json.dumps(_message_payload("https://youtu.be/abcdefghijk")).encode()
    headers = _signed(body)
    headers["X-Lark-Signature"] = "0" * 64
    resp = await client.post("/api/hooks/feishu", content=body, headers=headers)
    assert resp.status_code == 401


async def test_invalid_token(client: AsyncClient, enable_feishu):
    body = json.dumps({
        "challenge": "x", "token": "wrong-token", "type": "url_verification",
    }).encode()
    resp = await client.post("/api/hooks/feishu", content=body, headers=_signed(body))
    assert resp.status_code == 401


def test_extract_urls_text_and_markdown():
    assert "https://youtu.be/abcdefghijk" in extract_urls("see https://youtu.be/abcdefghijk now")
    assert extract_urls("[t](https://www.bilibili.com/video/BV1xx411c7mD)") == [
        "https://www.bilibili.com/video/BV1xx411c7mD",
    ]


def test_extract_urls_from_post():
    content = json.dumps({
        "title": "",
        "content": [[{"tag": "a", "href": "https://www.xiaoyuzhoufm.com/episode/507f1f77bcf86cd799439011"}]],
    })
    urls = extract_urls_from_message("post", content)
    picked = pick_supported_url(urls)
    assert picked is not None
    assert picked[1] == "xiaoyuzhou"


def test_unsupported_url_not_picked():
    assert pick_supported_url(["https://example.com/watch"]) is None


async def test_whitelist_rejects(db_session, enable_feishu, monkeypatch):
    reset_feishu_runtime_state()
    replies: list[dict] = []

    async def fake_reply(_creds, _mid, card):
        replies.append(card)

    monkeypatch.setattr(
        "video_split.service.feishu.feishu_client.reply_card",
        fake_reply,
    )
    await handle_message_event(
        _message_payload("https://www.youtube.com/watch?v=dQw4w9WgXcQ", open_id="ou_stranger"),
        db_session,
    )
    assert replies
    assert replies[0]["header"]["title"]["content"] == "未授权"


async def test_message_id_dedup(db_session, enable_feishu, monkeypatch):
    reset_feishu_runtime_state()
    from video_split.service.auth_service import ensure_admin_user

    await ensure_admin_user(db_session)
    started: list[str] = []

    async def fake_start(_db, _user, url):
        started.append(url)
        return {"task_id": 7, "platform": "youtube"}

    monkeypatch.setattr("video_split.api.analysis.start_analysis_for_user", fake_start)
    monkeypatch.setattr("video_split.service.feishu.feishu_client.reply_card", AsyncMock())
    monkeypatch.setattr("video_split.service.feishu.follow_task", AsyncMock())

    payload = _message_payload("https://www.youtube.com/watch?v=dQw4w9WgXcQ", message_id="om_dup")
    await handle_message_event(payload, db_session)
    await handle_message_event(payload, db_session)
    assert started == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]


async def test_unsupported_url_gets_help(db_session, enable_feishu, monkeypatch):
    reset_feishu_runtime_state()
    replies: list[dict] = []

    async def fake_reply(_creds, _mid, card):
        replies.append(card)

    monkeypatch.setattr("video_split.service.feishu.feishu_client.reply_card", fake_reply)
    await handle_message_event(_message_payload("https://example.com/nope"), db_session)
    assert replies[0]["header"]["title"]["content"] == "发一个视频链接"


async def test_handle_message_starts_analysis(db_session, enable_feishu, monkeypatch):
    reset_feishu_runtime_state()
    from video_split.service.auth_service import ensure_admin_user

    await ensure_admin_user(db_session)
    started: dict = {}

    async def fake_start(_db, user, url):
        started["url"] = url
        started["user"] = user.username
        return {"task_id": 42, "platform": "youtube"}

    replies: list[dict] = []

    async def fake_reply(_creds, _mid, card):
        replies.append(card)

    follow = AsyncMock()
    monkeypatch.setattr("video_split.api.analysis.start_analysis_for_user", fake_start)
    monkeypatch.setattr("video_split.service.feishu.feishu_client.reply_card", fake_reply)
    monkeypatch.setattr("video_split.service.feishu.follow_task", follow)

    await handle_message_event(
        _message_payload("看这个 https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        db_session,
    )
    assert started["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert started["user"] == "admin"
    assert replies[0]["header"]["title"]["content"] == "已开始分析"
    await asyncio.sleep(0)
    follow.assert_awaited()


async def test_text_confirm_pending_task(db_session, enable_feishu, monkeypatch):
    reset_feishu_runtime_state()
    rt = RunningTask(task_id=9, user_id=1, platform="bilibili", url="https://bilibili.com/video/BVxxxxxxxx")
    rt.last_progress = {"event": "confirm_required"}
    runner._tasks[9] = rt
    remember_origin(9, "om_old", "oc_1", ALLOWED_OPEN_ID)
    replies: list[dict] = []

    async def fake_reply(_creds, _mid, card):
        replies.append(card)

    monkeypatch.setattr("video_split.service.feishu.feishu_client.reply_card", fake_reply)
    try:
        await handle_message_event(_message_payload("确认", message_id="om_confirm"), db_session)
        assert rt.confirm_event.is_set()
        assert replies[0]["header"]["title"]["content"] == "已确认"
    finally:
        runner._tasks.pop(9, None)


async def test_text_confirm_without_pending_gets_help(db_session, enable_feishu, monkeypatch):
    reset_feishu_runtime_state()
    replies: list[dict] = []

    async def fake_reply(_creds, _mid, card):
        replies.append(card)

    monkeypatch.setattr("video_split.service.feishu.feishu_client.reply_card", fake_reply)
    await handle_message_event(_message_payload("确认", message_id="om_lonely"), db_session)
    assert replies[0]["header"]["title"]["content"] == "发一个视频链接"


async def test_confirm_card_action(enable_feishu, monkeypatch):
    monkeypatch.setattr(runner, "confirm", lambda task_id: task_id == 9)
    payload = {
        "header": {"event_type": "card.action.trigger", "token": VERIFICATION},
        "event": {
            "operator": {"open_id": ALLOWED_OPEN_ID},
            "action": {"value": {"action": "confirm", "task_id": "9"}, "tag": "button"},
        },
    }
    result = await handle_card_action(payload)
    assert result["toast"]["type"] == "success"


async def test_confirm_card_action_forbidden(enable_feishu, monkeypatch):
    monkeypatch.setattr(runner, "confirm", lambda _id: True)
    payload = {
        "header": {"event_type": "card.action.trigger", "token": VERIFICATION},
        "event": {
            "operator": {"open_id": "ou_other"},
            "action": {"value": {"action": "confirm", "task_id": "9"}},
        },
    }
    result = await handle_card_action(payload)
    assert result["toast"]["type"] == "error"


def test_secrets_upgraded_encrypted_and_0600(enable_feishu):
    creds = load_feishu_credentials()
    assert creds is not None
    assert creds.app_secret == APP_SECRET
    stored = yaml.safe_load(enable_feishu["secrets"].read_text())
    assert is_encrypted(stored["app_secret"])
    assert APP_SECRET not in enable_feishu["secrets"].read_text()
    assert (os.stat(enable_feishu["secrets"]).st_mode & 0o777) == 0o600
    kek = enable_feishu["config_dir"] / "secret.key"
    assert kek.exists()
    assert (os.stat(kek).st_mode & 0o777) == 0o600


async def test_http_confirm_and_no_secret_echo(client: AsyncClient, enable_feishu, monkeypatch):
    monkeypatch.setattr(runner, "confirm", lambda task_id: task_id == 3)
    payload = {
        "schema": "2.0",
        "header": {"event_type": "card.action.trigger", "token": VERIFICATION},
        "event": {
            "operator": {"open_id": ALLOWED_OPEN_ID},
            "action": {"value": {"action": "confirm", "task_id": "3"}},
        },
    }
    body = json.dumps(payload).encode()
    resp = await client.post("/api/hooks/feishu", content=body, headers=_signed(body))
    assert resp.status_code == 200
    assert resp.json()["toast"]["type"] == "success"
    blob = resp.text
    assert APP_SECRET not in blob
    assert ENCRYPT_KEY not in blob
    assert VERIFICATION not in blob


async def test_follow_task_confirm_then_complete(enable_feishu, monkeypatch):
    from video_split.schemas import ProgressEvent

    replies: list[str] = []

    async def fake_reply(_creds, _mid, card):
        replies.append(card["header"]["title"]["content"])

    monkeypatch.setattr("video_split.service.feishu.feishu_client.reply_card", fake_reply)
    monkeypatch.setattr("video_split.service.feishu.feishu_client.reply_text", AsyncMock())
    monkeypatch.setattr("video_split.service.feishu.feishu_client.send_text", AsyncMock())
    monkeypatch.setattr(
        "video_split.service.feishu._load_video_blurb",
        AsyncMock(return_value=("视频标题", "一句摘要")),
    )

    async def gen(_cancel, _confirm):
        yield ProgressEvent(
            stage="confirm_required", progress=10, message="超过阈值",
            detail={"task_id": 88, "title": "长视频"},
        )
        yield ProgressEvent(
            stage="complete", progress=100, message="done",
            detail={"video_id": 5},
        )

    creds = load_feishu_credentials()
    assert creds is not None
    runner.start(88, 1, "youtube", "https://youtu.be/abcdefghijk", lambda c, k: gen(c, k))
    try:
        await follow_task(88, creds, "om_follow", "oc_follow")
    finally:
        runner.remove(88)
    assert replies == ["需要确认", "分析完成"]


async def test_notify_ok_web_dms_whitelist(enable_feishu, monkeypatch):
    sent: list[tuple[str, str]] = []

    async def fake_send(_creds, receive_id, text, *, receive_id_type="open_id"):
        sent.append((receive_id, text))

    monkeypatch.setattr("video_split.service.feishu.feishu_client.send_text", fake_send)
    monkeypatch.setattr("video_split.service.feishu.feishu_client.reply_text", AsyncMock())
    await notify_complete_ok(201)
    assert sent == [(ALLOWED_OPEN_ID, "ok")]


async def test_notify_ok_feishu_replies_thread(enable_feishu, monkeypatch):
    replies: list[tuple[str, str]] = []

    async def fake_reply(_creds, message_id, text):
        replies.append((message_id, text))

    send = AsyncMock()
    monkeypatch.setattr("video_split.service.feishu.feishu_client.reply_text", fake_reply)
    monkeypatch.setattr("video_split.service.feishu.feishu_client.send_text", send)
    remember_origin(202, "om_src", "oc_src", ALLOWED_OPEN_ID)
    await notify_complete_ok(202)
    assert replies == [("om_src", "ok")]
    send.assert_not_awaited()


async def test_runner_complete_notifies_ok(enable_feishu, monkeypatch):
    from video_split.schemas import ProgressEvent

    sent: list[tuple[str, str]] = []

    async def fake_send(_creds, receive_id, text, *, receive_id_type="open_id"):
        sent.append((receive_id, text))

    monkeypatch.setattr("video_split.service.feishu.feishu_client.send_text", fake_send)
    monkeypatch.setattr("video_split.service.feishu.feishu_client.reply_text", AsyncMock())

    async def gen(_cancel, _confirm):
        yield ProgressEvent(stage="complete", progress=100, message="done", detail={"video_id": 1})

    runner.start(203, 1, "youtube", "https://youtu.be/abcdefghijk", lambda c, k: gen(c, k))
    try:
        for _ in range(20):
            if sent:
                break
            await asyncio.sleep(0.02)
    finally:
        runner.remove(203)
    assert sent == [(ALLOWED_OPEN_ID, "ok")]


def test_result_card_has_note_button(enable_feishu):
    card = card_result("标题", "摘要", "https://notes.example/share/9?sig=abc")
    actions = card["elements"][1]["actions"]
    assert actions[0]["url"] == "https://notes.example/share/9?sig=abc"
    assert actions[0]["text"]["content"] == "查看全文"


def test_result_note_url_is_signed(enable_feishu):
    url = result_note_url(9)
    assert url.startswith("https://notes.example/share/9?sig=")
    assert "sig=" in url
    from video_split.share_token import verify_view_sig

    sig = url.split("sig=", 1)[1]
    assert verify_view_sig(9, sig)
    assert not verify_view_sig(9, "0" * 32)


def test_settings_do_not_hold_feishu_secrets(enable_feishu):
    settings = get_settings()
    dumped = settings.model_dump_json()
    assert APP_SECRET not in dumped
    assert ENCRYPT_KEY not in dumped
    assert VERIFICATION not in dumped
