"""Inbound Feishu event webhook. Hidden from external API docs (tag: hooks)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from video_split.config import get_settings
from video_split.service.feishu import (
    FeishuProtocolError,
    decode_payload,
    event_token,
    handle_card_action,
    load_feishu_credentials,
    run_message_event,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hooks"])


def _is_url_verification(payload: dict) -> bool:
    return payload.get("type") == "url_verification" or (
        "challenge" in payload and not payload.get("header") and not payload.get("event")
    )


def _event_type(payload: dict) -> str:
    header = payload.get("header")
    if isinstance(header, dict) and header.get("event_type"):
        return str(header["event_type"])
    event = payload.get("event")
    if isinstance(event, dict) and event.get("type"):
        return str(event["type"])
    if payload.get("action"):
        return "card.action.trigger"
    return ""


@router.post("/api/hooks/feishu")
async def feishu_hook(request: Request):
    """Feishu event + card-action callback. Not part of the public API."""
    settings = get_settings()
    if not settings.feishu.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    creds = load_feishu_credentials()
    if creds is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Feishu is not configured")

    raw = await request.body()
    try:
        payload = decode_payload(raw, request.headers, creds)
    except FeishuProtocolError:
        # Console URL check often omits X-Lark-* headers (or the proxy drops them).
        # Still echo challenge if we can parse / decrypt a url_verification body.
        try:
            payload = decode_payload(raw, request.headers, creds, require_signature=False)
        except FeishuProtocolError:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature")
        if not _is_url_verification(payload):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature")
        logger.info("[feishu] url_verification accepted without signature headers")

    token = event_token(payload)
    if token and token != creds.verification_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")

    if _is_url_verification(payload):
        if payload.get("token") and payload["token"] != creds.verification_token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
        return {"challenge": payload.get("challenge", "")}

    if token != creds.verification_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")

    event_type = _event_type(payload)
    if event_type == "card.action.trigger":
        return await handle_card_action(payload)

    if event_type in {"im.message.receive_v1", "im.message.receive_v2"}:
        asyncio.create_task(run_message_event(payload))
        return JSONResponse({})

    logger.info("[feishu] ignored event_type=%s", event_type or "unknown")
    return JSONResponse({})
