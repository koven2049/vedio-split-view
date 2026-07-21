from __future__ import annotations

import logging
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from video_split.config import get_settings
from video_split.dependencies import require_authenticated
from video_split.models import User
from video_split.service.downloader import _apply_youtube_opts

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/youtube", tags=["youtube"])

_YT_PROBE_VIDEO_ID = "jNQXAC9IVRw"
_BOT_HINT = "not a bot"


class CookiesStatusOut(BaseModel):
    configured: bool = False
    file_exists: bool = False
    earliest_expiry: str | None = None
    earliest_expiry_ts: int | None = None
    expired: bool = False
    cookie_count: int = 0
    domain_summary: str = ""
    usability_checked: bool = False
    usable: bool | None = None
    usability_message: str = ""
    checked_at: str | None = None


def _probe_youtube_cookiefile() -> tuple[bool | None, str]:
    """Check whether the configured YouTube cookies can pass a lightweight yt-dlp probe."""
    import yt_dlp

    settings = get_settings()
    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "socket_timeout": 20,
        "extractor_retries": 1,
    }
    if settings.network.proxy_enabled and settings.network.http_proxy:
        ydl_opts["proxy"] = settings.network.http_proxy
    else:
        ydl_opts["proxy"] = ""
    _apply_youtube_opts(ydl_opts)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={_YT_PROBE_VIDEO_ID}",
                download=False,
            )
        if info:
            return True, "Cookies look usable for yt-dlp metadata requests."
        return None, "Probe completed but returned no metadata."
    except Exception as exc:
        message = str(exc).strip()
        lower = message.lower()
        if _BOT_HINT in lower or "sign in to confirm" in lower:
            return False, "Configured cookies are present, but YouTube still requires bot/login verification."
        if "cookie" in lower and ("expired" in lower or "invalid" in lower):
            return False, "Configured cookies were rejected by YouTube."
        if "proxy" in lower:
            return None, f"Probe could not reach YouTube through the configured proxy: {message}"
        return None, f"Probe failed: {message}"


def _parse_cookies_file(path: str) -> list[dict]:
    """Parse Netscape cookies.txt format."""
    p = Path(path)
    if not p.is_absolute():
        p = Path("config") / p
    if not p.exists():
        return []

    cookies = []
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        try:
            cookies.append({
                "domain": parts[0],
                "name": parts[5],
                "expiry": int(parts[4]),
            })
        except (ValueError, IndexError):
            continue
    return cookies


@router.get("/cookies-status", response_model=CookiesStatusOut)
async def cookies_status(_user: User = Depends(require_authenticated)):
    settings = get_settings()
    path_str = settings.network.youtube_cookies_file
    checked_at = datetime.now(tz=timezone.utc).isoformat()
    if not path_str:
        return CookiesStatusOut(checked_at=checked_at)

    p = Path(path_str)
    if not p.is_absolute():
        p = Path("config") / p

    if not p.exists():
        return CookiesStatusOut(configured=True, file_exists=False, checked_at=checked_at)

    cookies = _parse_cookies_file(path_str)
    if not cookies:
        return CookiesStatusOut(configured=True, file_exists=True, checked_at=checked_at)

    yt_cookies = [c for c in cookies if "youtube" in c["domain"] or "google" in c["domain"]]
    auth_names = {"SID", "HSID", "SSID", "APISID", "SAPISID", "LOGIN_INFO", "__Secure-1PSID", "__Secure-3PSID"}
    auth_cookies = [c for c in yt_cookies if c["name"] in auth_names and c["expiry"] > 0]

    if not auth_cookies:
        relevant = [c for c in yt_cookies if c["expiry"] > 0]
    else:
        relevant = auth_cookies

    if not relevant:
        return CookiesStatusOut(
            configured=True, file_exists=True,
            cookie_count=len(cookies),
            domain_summary=f"{len(yt_cookies)} YouTube/Google cookies",
            checked_at=checked_at,
        )

    earliest = min(relevant, key=lambda c: c["expiry"])
    expiry_dt = datetime.fromtimestamp(earliest["expiry"], tz=timezone.utc)
    now = datetime.now(tz=timezone.utc)
    usable: bool | None = None
    usability_message = ""
    usability_checked = False

    if expiry_dt >= now:
        usability_checked = True
        usable, usability_message = await asyncio.get_running_loop().run_in_executor(
            None, _probe_youtube_cookiefile,
        )

    return CookiesStatusOut(
        configured=True,
        file_exists=True,
        earliest_expiry=expiry_dt.isoformat(),
        earliest_expiry_ts=earliest["expiry"],
        expired=expiry_dt < now,
        cookie_count=len(yt_cookies),
        domain_summary=f"{len(auth_cookies)} auth cookies, earliest: {earliest['name']}",
        usability_checked=usability_checked,
        usable=usable,
        usability_message=usability_message,
        checked_at=checked_at,
    )
