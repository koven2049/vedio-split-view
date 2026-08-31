"""Feishu app-bot adapter: verify events, extract URLs, start analysis, reply cards."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import yaml
from Crypto.Cipher import AES
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from video_split.config import get_settings
from video_split.secure import decrypt, encrypt, ensure_mode_0600, is_encrypted, load_or_create_kek
from video_split.service.downloader import detect_platform, normalize_url
from video_split.service.task_runner import runner

logger = logging.getLogger(__name__)

SECRET_FIELDS = frozenset({"app_secret", "verification_token", "encrypt_key"})
_DEDUP_TTL = 600.0
_TOKEN_SKEW = 60.0
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)]+)\)")
_FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_FEISHU_REPLY_URL = "https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply"
_FEISHU_SEND_URL = "https://open.feishu.cn/open-apis/im/v1/messages"

_seen_message_ids: dict[str, float] = {}
_token_cache: dict[str, Any] = {"token": "", "exp": 0.0}
# task_id → (message_id, chat_id, open_id) for Feishu-originated jobs
_origins: dict[int, tuple[str, str, str]] = {}


class FeishuProtocolError(Exception):
    """Signature / decrypt / token failure on an inbound Feishu request."""


@dataclass(frozen=True)
class FeishuCredentials:
    app_id: str
    app_secret: str
    verification_token: str
    encrypt_key: str


def reset_feishu_runtime_state() -> None:
    """Test helper: drop dedup + token + credentials caches."""
    _seen_message_ids.clear()
    _origins.clear()
    _token_cache["token"] = ""
    _token_cache["exp"] = 0.0
    load_feishu_credentials.cache_clear()


def remember_origin(task_id: int, message_id: str, chat_id: str, open_id: str) -> None:
    _origins[task_id] = (message_id, chat_id, open_id)


def _config_dir() -> Path:
    from video_split.config import _resolve_config_path

    return _resolve_config_path().parent


def _secrets_path() -> Path:
    settings = get_settings()
    name = settings.feishu.secrets_file or "feishu.yaml"
    return _config_dir() / name


def _kek_path() -> Path:
    return _config_dir() / "secret.key"


def _write_secrets_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    ensure_mode_0600(path)


@lru_cache
def load_feishu_credentials() -> FeishuCredentials | None:
    path = _secrets_path()
    if not path.exists():
        return None
    ensure_mode_0600(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return None
    kek = load_or_create_kek(_kek_path())
    upgraded = False
    decrypted: dict[str, str] = {}
    stored: dict[str, Any] = dict(raw)
    app_id = str(raw.get("app_id") or "")
    for field in SECRET_FIELDS:
        value = str(raw.get(field) or "")
        if value and not is_encrypted(value):
            stored[field] = encrypt(value, kek)
            decrypted[field] = value
            upgraded = True
        else:
            decrypted[field] = decrypt(value, kek) if value else ""
    if upgraded:
        stored["app_id"] = app_id
        _write_secrets_file(path, stored)
    if not app_id or not decrypted.get("app_secret") or not decrypted.get("verification_token"):
        return None
    return FeishuCredentials(
        app_id=app_id,
        app_secret=decrypted["app_secret"],
        verification_token=decrypted["verification_token"],
        encrypt_key=decrypted.get("encrypt_key") or "",
    )


def compute_signature(timestamp: str, nonce: str, encrypt_key: str, body: bytes) -> str:
    blob = timestamp.encode() + nonce.encode() + encrypt_key.encode() + body
    return hashlib.sha256(blob).hexdigest()


def verify_signature(
    timestamp: str, nonce: str, encrypt_key: str, body: bytes, signature: str,
) -> bool:
    if not signature:
        return False
    expected = compute_signature(timestamp, nonce, encrypt_key, body)
    return hmac.compare_digest(expected, signature)


def decrypt_event(encrypt_b64: str, encrypt_key: str) -> str:
    key = hashlib.sha256(encrypt_key.encode()).digest()
    raw = base64.b64decode(encrypt_b64)
    iv, ct = raw[: AES.block_size], raw[AES.block_size :]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    padded = cipher.decrypt(ct)
    pad = padded[-1]
    if pad < 1 or pad > AES.block_size or padded[-pad:] != bytes([pad]) * pad:
        raise FeishuProtocolError("invalid padding")
    return padded[:-pad].decode("utf-8")


def encrypt_event(plaintext: str, encrypt_key: str) -> str:
    """Test helper matching Feishu AES-256-CBC + SHA256(key) + PKCS7."""
    key = hashlib.sha256(encrypt_key.encode()).digest()
    iv = hashlib.sha256(b"test-iv" + encrypt_key.encode()).digest()[:16]
    data = plaintext.encode()
    pad = AES.block_size - (len(data) % AES.block_size)
    padded = data + bytes([pad]) * pad
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return base64.b64encode(iv + cipher.encrypt(padded)).decode()


def _header(headers: Any, name: str) -> str:
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if callable(getter):
        val = getter(name)
        if val:
            return str(val)
    try:
        for key in headers:
            if str(key).lower() == name:
                return str(headers[key])
    except TypeError:
        pass
    return ""


def decode_payload(
    body: bytes,
    headers: Any,
    creds: FeishuCredentials,
    *,
    require_signature: bool = True,
) -> dict[str, Any]:
    timestamp = _header(headers, "x-lark-request-timestamp")
    nonce = _header(headers, "x-lark-request-nonce")
    signature = _header(headers, "x-lark-signature")
    if require_signature:
        if creds.encrypt_key:
            if not verify_signature(timestamp, nonce, creds.encrypt_key, body, signature):
                raise FeishuProtocolError("invalid signature")
        elif signature:
            if not verify_signature(timestamp, nonce, "", body, signature):
                raise FeishuProtocolError("invalid signature")

    try:
        outer = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError as e:
        raise FeishuProtocolError("invalid json") from e
    if not isinstance(outer, dict):
        raise FeishuProtocolError("invalid json")

    if "encrypt" in outer:
        if not creds.encrypt_key:
            raise FeishuProtocolError("encrypted event without encrypt_key")
        try:
            inner = json.loads(decrypt_event(str(outer["encrypt"]), creds.encrypt_key))
        except FeishuProtocolError:
            raise
        except Exception as e:
            raise FeishuProtocolError("decrypt failed") from e
        if not isinstance(inner, dict):
            raise FeishuProtocolError("invalid decrypted json")
        return inner
    return outer


def event_token(payload: dict[str, Any]) -> str:
    header = payload.get("header")
    if isinstance(header, dict) and header.get("token"):
        return str(header["token"])
    return str(payload.get("token") or "")


def extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    for m in _MD_LINK_RE.finditer(text):
        urls.append(m.group(2).rstrip(").,]>\"'"))
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(").,]>\"'")
        if url not in urls:
            urls.append(url)
    return urls


def extract_urls_from_message(message_type: str, content: str) -> list[str]:
    try:
        parsed = json.loads(content) if content else {}
    except json.JSONDecodeError:
        return extract_urls(content)

    urls: list[str] = []
    if message_type == "text":
        urls.extend(extract_urls(str(parsed.get("text") or "")))
        return urls

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            href = node.get("href")
            if href:
                urls.append(str(href))
            text = node.get("text")
            if text:
                urls.extend(extract_urls(str(text)))
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(parsed)
    return urls


def pick_supported_url(urls: list[str]) -> tuple[str, str] | None:
    for raw in urls:
        url = normalize_url(raw)
        platform, _vid = detect_platform(url)
        if platform != "unknown":
            return url, platform
    return None


def already_seen(message_id: str) -> bool:
    now = time.monotonic()
    stale = [key for key, seen_at in _seen_message_ids.items() if now - seen_at > _DEDUP_TTL]
    for key in stale:
        _seen_message_ids.pop(key, None)
    if message_id in _seen_message_ids:
        return True
    _seen_message_ids[message_id] = now
    return False


def _md_card(title: str, body: str, *, color: str = "blue", buttons: list[dict] | None = None) -> dict:
    elements: list[dict] = [{"tag": "div", "text": {"tag": "lark_md", "content": body}}]
    if buttons:
        elements.append({"tag": "action", "actions": buttons})
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": color},
        "elements": elements,
    }


def card_started(url: str, platform: str) -> dict:
    return _md_card("已开始分析", f"平台：**{platform}**\n{url}")


def card_help() -> dict:
    return _md_card(
        "发一个视频链接",
        "只支持 YouTube / B站 / 小宇宙。私聊直接粘贴；群里先 @ 我再贴链接。",
        color="grey",
    )


def card_unauthorized() -> dict:
    return _md_card("未授权", "你不在白名单里，无法触发分析。", color="red")


def card_error(message: str) -> dict:
    return _md_card("分析失败", message or "未知错误", color="red")


def card_result(title: str, summary: str, note_url: str) -> dict:
    body = f"**{title or '分析完成'}**"
    if summary:
        clip = summary.strip().replace("\n", " ")
        if len(clip) > 280:
            clip = clip[:277] + "…"
        body += f"\n{clip}"
    buttons = []
    if note_url:
        buttons.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看全文"},
            "type": "primary",
            "url": note_url,
        })
    return _md_card("分析完成", body, color="green", buttons=buttons)


def card_confirm(task_id: int, message: str, title: str) -> dict:
    body = f"**{title}**\n{message}" if title else message
    body += "\n也可以直接回复「确认」。"
    return _md_card(
        "需要确认",
        body,
        color="orange",
        buttons=[{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "确认继续"},
            "type": "primary",
            "value": {"action": "confirm", "task_id": str(task_id)},
        }],
    )


def card_confirmed() -> dict:
    return _md_card("已确认", "继续分析中。", color="green")


_CONFIRM_PHRASES = frozenset({"确认", "继续", "确认继续", "confirm", "yes", "y"})


def _is_confirm_text(text: str) -> bool:
    return text.strip().lower() in _CONFIRM_PHRASES


def _message_plain_text(message_type: str, content: str) -> str:
    try:
        parsed = json.loads(content) if content else {}
    except json.JSONDecodeError:
        return content.strip()
    if isinstance(parsed, dict) and message_type == "text":
        return str(parsed.get("text") or "").strip()
    return ""


def _task_awaiting_confirm(task_id: int) -> bool:
    rt = runner.get(task_id)
    if rt is None or rt.finished or rt.confirm_event.is_set():
        return False
    return (rt.last_progress or {}).get("event") == "confirm_required"


def pending_confirm_task_id(open_id: str) -> int | None:
    for task_id, origin in _origins.items():
        if origin[2] == open_id and _task_awaiting_confirm(task_id):
            return task_id
    return None


def result_note_url(video_id: int) -> str:
    base = get_settings().feishu.result_base_url.rstrip("/")
    if not base:
        return ""
    from video_split.share_token import make_view_sig

    sig = make_view_sig(video_id)
    return f"{base}/share/{video_id}?sig={sig}"


def is_open_id_allowed(open_id: str) -> bool:
    allowed = get_settings().feishu.allowed_open_ids
    return bool(open_id) and open_id in allowed


class FeishuClient:
    async def _tenant_token(self, creds: FeishuCredentials) -> str:
        now = time.time()
        if _token_cache["token"] and now < float(_token_cache["exp"]) - _TOKEN_SKEW:
            return str(_token_cache["token"])
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _FEISHU_TOKEN_URL,
                json={"app_id": creds.app_id, "app_secret": creds.app_secret},
            )
            resp.raise_for_status()
            data = resp.json()
        token = data.get("tenant_access_token") or ""
        if not token:
            raise RuntimeError("feishu tenant token missing")
        _token_cache["token"] = token
        _token_cache["exp"] = now + float(data.get("expire") or 7200)
        return token

    async def reply_card(self, creds: FeishuCredentials, message_id: str, card: dict) -> None:
        token = await self._tenant_token(creds)
        url = _FEISHU_REPLY_URL.format(message_id=message_id)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"content": json.dumps(card, ensure_ascii=False), "msg_type": "interactive"},
            )
            if resp.status_code >= 400:
                logger.error("[feishu] reply failed status=%s", resp.status_code)

    async def send_card(self, creds: FeishuCredentials, chat_id: str, card: dict) -> None:
        token = await self._tenant_token(creds)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _FEISHU_SEND_URL,
                headers={"Authorization": f"Bearer {token}"},
                params={"receive_id_type": "chat_id"},
                json={
                    "receive_id": chat_id,
                    "msg_type": "interactive",
                    "content": json.dumps(card, ensure_ascii=False),
                },
            )
            if resp.status_code >= 400:
                logger.error("[feishu] send failed status=%s", resp.status_code)

    async def reply_text(self, creds: FeishuCredentials, message_id: str, text: str) -> None:
        token = await self._tenant_token(creds)
        url = _FEISHU_REPLY_URL.format(message_id=message_id)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"content": json.dumps({"text": text}, ensure_ascii=False), "msg_type": "text"},
            )
            if resp.status_code >= 400:
                logger.error("[feishu] reply text failed status=%s", resp.status_code)

    async def send_text(
        self,
        creds: FeishuCredentials,
        receive_id: str,
        text: str,
        *,
        receive_id_type: str = "open_id",
    ) -> None:
        token = await self._tenant_token(creds)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _FEISHU_SEND_URL,
                headers={"Authorization": f"Bearer {token}"},
                params={"receive_id_type": receive_id_type},
                json={
                    "receive_id": receive_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                },
            )
            if resp.status_code >= 400:
                logger.error("[feishu] send text failed status=%s", resp.status_code)


feishu_client = FeishuClient()


async def notify_complete_ok(task_id: int) -> None:
    """Reply/send plain 'ok' after any analysis completes (web or Feishu)."""
    settings = get_settings()
    if not settings.feishu.enabled:
        return
    creds = load_feishu_credentials()
    if creds is None:
        return
    origin = _origins.pop(task_id, None)
    try:
        if origin and origin[0]:
            await feishu_client.reply_text(creds, origin[0], "ok")
            return
        for open_id in settings.feishu.allowed_open_ids:
            if open_id:
                await feishu_client.send_text(creds, open_id, "ok")
    except Exception:
        logger.exception("[feishu] failed to send ok")


def _on_runner_event(rt: Any, entry: dict[str, Any]) -> None:
    if entry.get("event") != "complete":
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(notify_complete_ok(rt.task_id))


runner.add_event_listener(_on_runner_event)


def _sender_open_id(event: dict[str, Any]) -> str:
    sender = event.get("sender") or {}
    sid = sender.get("sender_id") or {}
    return str(sid.get("open_id") or "")


def _card_open_id(payload: dict[str, Any]) -> str:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else payload
    operator = event.get("operator") if isinstance(event, dict) else None
    if isinstance(operator, dict) and operator.get("open_id"):
        return str(operator["open_id"])
    if isinstance(event, dict) and event.get("open_id"):
        return str(event["open_id"])
    return str(payload.get("open_id") or "")


def _card_action_value(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event") if isinstance(payload.get("event"), dict) else payload
    action = (event or {}).get("action") if isinstance(event, dict) else payload.get("action")
    if not isinstance(action, dict):
        return {}
    value = action.get("value") or {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


async def handle_card_action(payload: dict[str, Any]) -> dict[str, Any]:
    open_id = _card_open_id(payload)
    if not is_open_id_allowed(open_id):
        return {"toast": {"type": "error", "content": "未授权"}}
    value = _card_action_value(payload)
    if value.get("action") != "confirm":
        return {}
    try:
        task_id = int(value.get("task_id"))
    except (TypeError, ValueError):
        return {"toast": {"type": "error", "content": "无效的任务"}}
    if runner.confirm(task_id):
        return {"toast": {"type": "success", "content": "已确认，继续分析"}}
    return {"toast": {"type": "error", "content": "没有待确认的任务"}}


async def _reply(creds: FeishuCredentials, message_id: str, chat_id: str, card: dict) -> None:
    try:
        if message_id:
            await feishu_client.reply_card(creds, message_id, card)
        elif chat_id:
            await feishu_client.send_card(creds, chat_id, card)
    except Exception:
        logger.exception("[feishu] failed to send card")


async def follow_task(
    task_id: int, creds: FeishuCredentials, message_id: str, chat_id: str,
) -> None:
    sub = runner.subscribe(task_id)
    if sub is None:
        await _reply(creds, message_id, chat_id, card_error("任务已丢失，请重试"))
        return
    replay, queue = sub
    sent_confirm = False

    async def _handle(entry: dict[str, Any] | None) -> None:
        nonlocal sent_confirm
        if entry is None:
            return
        try:
            data = json.loads(entry.get("data") or "{}")
        except json.JSONDecodeError:
            data = {}
        stage = str(data.get("stage") or entry.get("event") or "")
        detail = data.get("detail") if isinstance(data.get("detail"), dict) else {}
        if stage == "confirm_required" and not sent_confirm:
            sent_confirm = True
            await _reply(
                creds, message_id, chat_id,
                card_confirm(
                    int(detail.get("task_id") or task_id),
                    str(data.get("message") or "视频较长，确认继续？"),
                    str(detail.get("title") or ""),
                ),
            )
        elif stage == "complete":
            video_id = detail.get("video_id")
            title, summary = "", ""
            if video_id:
                title, summary = await _load_video_blurb(int(video_id))
                note = result_note_url(int(video_id))
            else:
                note = ""
            await _reply(creds, message_id, chat_id, card_result(title, summary, note))
        elif stage in {"error", "cancelled"}:
            msg = str(data.get("message") or ("已取消" if stage == "cancelled" else "分析失败"))
            await _reply(creds, message_id, chat_id, card_error(msg))

    try:
        for entry in replay:
            await _handle(entry)
        while True:
            entry = await queue.get()
            if entry is None:
                break
            await _handle(entry)
    finally:
        runner.unsubscribe(task_id, queue)


async def _load_video_blurb(video_id: int) -> tuple[str, str]:
    from video_split.database import _get_session_factory
    from video_split.models import Video

    factory = _get_session_factory()
    async with factory() as db:
        result = await db.execute(select(Video).where(Video.id == video_id))
        video = result.scalar_one_or_none()
        if video is None:
            return "", ""
        return video.title or "", video.summary or video.essence or ""


async def handle_message_event(payload: dict[str, Any], db: AsyncSession) -> None:
    creds = load_feishu_credentials()
    if creds is None:
        return
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    if sender.get("sender_type") == "bot":
        return

    message_id = str(message.get("message_id") or "")
    chat_id = str(message.get("chat_id") or "")
    if not message_id:
        return
    if already_seen(message_id):
        logger.info("[feishu] skip duplicate message_id")
        return

    open_id = _sender_open_id(event)
    if not is_open_id_allowed(open_id):
        await _reply(creds, message_id, chat_id, card_unauthorized())
        return

    msg_type = str(message.get("message_type") or "text")
    content = str(message.get("content") or "")
    urls = extract_urls_from_message(msg_type, content)
    picked = pick_supported_url(urls)
    if picked is None:
        if _is_confirm_text(_message_plain_text(msg_type, content)):
            task_id = pending_confirm_task_id(open_id)
            if task_id is not None and runner.confirm(task_id):
                await _reply(creds, message_id, chat_id, card_confirmed())
                return
        await _reply(creds, message_id, chat_id, card_help())
        return
    url, platform = picked

    from video_split.api.analysis import start_analysis_for_user
    from video_split.models import User
    from fastapi import HTTPException

    result = await db.execute(select(User).where(User.username == "admin", User.role == "admin"))
    admin = result.scalar_one_or_none()
    if admin is None:
        await _reply(creds, message_id, chat_id, card_error("管理员账号未就绪"))
        return

    try:
        started = await start_analysis_for_user(db, admin, url)
    except HTTPException as e:
        await _reply(creds, message_id, chat_id, card_error(str(e.detail)))
        return

    await _reply(creds, message_id, chat_id, card_started(url, started.get("platform") or platform))
    task_id = int(started["task_id"])
    remember_origin(task_id, message_id, chat_id, open_id)
    asyncio.create_task(follow_task(task_id, creds, message_id, chat_id))


async def run_message_event(payload: dict[str, Any]) -> None:
    from video_split.database import _get_session_factory

    factory = _get_session_factory()
    async with factory() as db:
        try:
            await handle_message_event(payload, db)
        except Exception:
            logger.exception("[feishu] message handler failed")
