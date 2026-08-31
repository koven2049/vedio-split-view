"""HMAC view tokens for unauthenticated Feishu note links."""

from __future__ import annotations

import hashlib
import hmac

from video_split.config import get_settings

_PREFIX = "feishu-view:"


def make_view_sig(video_id: int) -> str:
    secret = get_settings().app.secret_key.encode()
    digest = hmac.new(secret, f"{_PREFIX}{video_id}".encode(), hashlib.sha256).hexdigest()
    return digest[:32]


def verify_view_sig(video_id: int, sig: str) -> bool:
    if not sig or len(sig) > 128:
        return False
    expected = make_view_sig(video_id)
    return hmac.compare_digest(expected, sig)
