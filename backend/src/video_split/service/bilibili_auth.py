from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass

import httpx
import qrcode

from video_split.config import get_settings

logger = logging.getLogger(__name__)

BILI_QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
BILI_QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

_BILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}


@dataclass
class QRCodeData:
    qr_key: str
    qr_url: str
    qr_image_base64: str


@dataclass
class QRPollResult:
    status: str  # "waiting", "scanned", "confirmed", "expired"
    sessdata: str = ""
    bili_jct: str = ""
    buvid3: str = ""


async def generate_qr_code() -> QRCodeData:
    settings = get_settings()
    proxy_conf = settings.network.proxy_url

    logger.info("[bilibili] Generating QR code via %s", BILI_QR_GENERATE_URL)
    async with httpx.AsyncClient(headers=_BILI_HEADERS, proxy=proxy_conf) as client:
        resp = await client.get(BILI_QR_GENERATE_URL)
        resp.raise_for_status()
        data = resp.json()

    qr_data = data["data"]
    qr_key = qr_data["qrcode_key"]
    qr_url = qr_data["url"]

    img = qrcode.make(qr_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    logger.info("[bilibili] QR code generated: qr_key=%s", qr_key)
    return QRCodeData(qr_key=qr_key, qr_url=qr_url, qr_image_base64=b64)


async def poll_qr_status(qr_key: str) -> QRPollResult:
    settings = get_settings()
    proxy_conf = settings.network.proxy_url

    async with httpx.AsyncClient(headers=_BILI_HEADERS, proxy=proxy_conf) as client:
        resp = await client.get(BILI_QR_POLL_URL, params={"qrcode_key": qr_key})
        resp.raise_for_status()
        data = resp.json()

    poll_data = data.get("data", {})
    code = poll_data.get("code", -1)

    if code == 0:
        url_str = poll_data.get("url", "")
        cookies = _parse_cookies_from_url(url_str)
        set_cookie_headers = resp.headers.get_list("set-cookie") if hasattr(resp.headers, "get_list") else []
        for header in set_cookie_headers:
            _parse_set_cookie(header, cookies)

        return QRPollResult(
            status="confirmed",
            sessdata=cookies.get("SESSDATA", ""),
            bili_jct=cookies.get("bili_jct", ""),
            buvid3=cookies.get("buvid3", ""),
        )
    elif code == 86038:
        return QRPollResult(status="expired")
    elif code == 86090:
        return QRPollResult(status="scanned")
    else:
        return QRPollResult(status="waiting")


def _parse_cookies_from_url(url: str) -> dict[str, str]:
    from urllib.parse import parse_qs, urlparse
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    return {k: v[0] for k, v in params.items()}


def _parse_set_cookie(header: str, cookies: dict[str, str]) -> None:
    parts = header.split(";")
    if parts:
        kv = parts[0].strip().split("=", 1)
        if len(kv) == 2:
            cookies[kv[0]] = kv[1]
