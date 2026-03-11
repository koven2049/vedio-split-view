from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from video_split.config import get_settings
from video_split.dependencies import require_user
from video_split.models import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/youtube", tags=["youtube"])


class CookiesStatusOut(BaseModel):
    configured: bool = False
    file_exists: bool = False
    earliest_expiry: str | None = None
    earliest_expiry_ts: int | None = None
    expired: bool = False
    cookie_count: int = 0
    domain_summary: str = ""


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
async def cookies_status(_user: User = Depends(require_user)):
    settings = get_settings()
    path_str = settings.network.youtube_cookies_file
    if not path_str:
        return CookiesStatusOut()

    p = Path(path_str)
    if not p.is_absolute():
        p = Path("config") / p

    if not p.exists():
        return CookiesStatusOut(configured=True, file_exists=False)

    cookies = _parse_cookies_file(path_str)
    if not cookies:
        return CookiesStatusOut(configured=True, file_exists=True)

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
        )

    earliest = min(relevant, key=lambda c: c["expiry"])
    expiry_dt = datetime.fromtimestamp(earliest["expiry"], tz=timezone.utc)
    now = datetime.now(tz=timezone.utc)

    return CookiesStatusOut(
        configured=True,
        file_exists=True,
        earliest_expiry=expiry_dt.isoformat(),
        earliest_expiry_ts=earliest["expiry"],
        expired=expiry_dt < now,
        cookie_count=len(yt_cookies),
        domain_summary=f"{len(auth_cookies)} auth cookies, earliest: {earliest['name']}",
    )
