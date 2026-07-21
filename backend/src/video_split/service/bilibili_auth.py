from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import qrcode

from video_split.config import get_settings

logger = logging.getLogger(__name__)

BILI_QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
BILI_QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

# --- Device-fingerprint endpoints (anti risk-control -352 mitigation) ---
BILI_FINGER_SPI_URL = "https://api.bilibili.com/x/frontend/finger/spi"
BILI_EXCLIMB_URL = "https://api.bilibili.com/x/internal/gaia-gateway/ExClimbWuzhi"
BILI_TICKET_URL = (
    "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket"
)
# HMAC key + key_id are community-known constants for the web bili_ticket sign.
# They are not secrets and may change if Bilibili rotates the scheme.
_TICKET_HMAC_KEY = b"XgwSnGZ1p"
_TICKET_KEY_ID = "ec02"

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


@dataclass
class Fingerprint:
    """Device fingerprint cookies used to reduce -352 risk-control on
    data-center IPs. Persisted to a small JSON file so it survives restarts
    and can be shared with the downloader (which has no DB access)."""

    buvid3: str = ""
    buvid4: str = ""
    b_nut: str = ""
    bili_ticket: str = ""
    ticket_expires_at: int = 0  # unix seconds; 0 == no ticket

    def as_cookie_dict(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.buvid3:
            out["buvid3"] = self.buvid3
        if self.buvid4:
            out["buvid4"] = self.buvid4
        if self.b_nut:
            out["b_nut"] = self.b_nut
        if self.bili_ticket and self.ticket_expires_at > int(time.time()):
            out["bili_ticket"] = self.bili_ticket
        return out


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

        # Seed / refresh the device fingerprint cache on successful login so
        # the downloader can attach buvid3/buvid4/b_nut/bili_ticket cookies.
        # Best-effort: a failure here must not fail the login.
        try:
            await refresh_fingerprint(cookies.get("bili_jct", ""))
        except Exception:
            logger.warning("[bilibili] fingerprint refresh after login failed", exc_info=True)

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


# --------------------------------------------------------------------------
# Device fingerprint: acquisition, activation, bili_ticket, persistence.
#
# These are best-effort risk-control mitigations. Every network step MUST
# fail loud (log a warning) and degrade gracefully — a failure here must
# never break QR login or a download; callers keep using whatever cookies
# they already have.
# --------------------------------------------------------------------------

def _fingerprint_path() -> Path:
    """JSON file storing the fingerprint, next to the DB / temp dir."""
    settings = get_settings()
    base = Path(settings.storage.temp_dir).parent
    return base / "bilibili_fingerprint.json"


def load_fingerprint() -> Fingerprint:
    """Load the cached fingerprint, or an empty one if none/corrupt."""
    path = _fingerprint_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return Fingerprint()
    return Fingerprint(
        buvid3=str(raw.get("buvid3", "")),
        buvid4=str(raw.get("buvid4", "")),
        b_nut=str(raw.get("b_nut", "")),
        bili_ticket=str(raw.get("bili_ticket", "")),
        ticket_expires_at=int(raw.get("ticket_expires_at", 0) or 0),
    )


def save_fingerprint(fp: Fingerprint) -> None:
    """Persist the fingerprint atomically. Non-fatal on failure."""
    path = _fingerprint_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "buvid3": fp.buvid3,
                    "buvid4": fp.buvid4,
                    "b_nut": fp.b_nut,
                    "bili_ticket": fp.bili_ticket,
                    "ticket_expires_at": fp.ticket_expires_at,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        logger.warning("[bilibili] Failed to persist fingerprint", exc_info=True)


async def fetch_fingerprint() -> tuple[str, str]:
    """Fetch a fresh (buvid3, buvid4) pair from the finger/spi endpoint.

    Returns ("", "") on any failure (fail loud, degrade).
    """
    settings = get_settings()
    try:
        async with httpx.AsyncClient(
            headers=_BILI_HEADERS, proxy=settings.network.proxy_url, timeout=20
        ) as client:
            resp = await client.get(BILI_FINGER_SPI_URL)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.warning("[bilibili] finger/spi fetch failed", exc_info=True)
        return "", ""

    if data.get("code") != 0:
        logger.warning("[bilibili] finger/spi non-zero code: %s", data.get("code"))
        return "", ""
    d = data.get("data", {})
    return str(d.get("b_3", "")), str(d.get("b_4", ""))


def _build_exclimb_payload(buvid3: str) -> dict[str, str]:
    """Build the ExClimbWuzhi activation payload.

    The real browser payload carries a large obfuscated fingerprint object.
    We send a minimal-but-well-formed payload; the primary goal is that the
    buvid3/buvid4 get "activated" server-side. Values here intentionally avoid
    hardcoding volatile browser fingerprints that would go stale.
    """
    inner = {
        "3064": 1,  # platform: web
        "5062": str(int(time.time() * 1000)),  # ts (ms)
        "03bf": "https://www.bilibili.com/",  # url
        "39c8": "333.788.fp.risk",  # spm
        "34f1": "",
        "d402": "",
        "654a": "",
        "6e7c": "1920x1080",  # resolution
        "3c43": {"adca": "Mac", "bfe9": buvid3},
        "07a4": "zh-CN",
        "5f45": None,
        "db46": 0,
    }
    return {"payload": json.dumps(inner, separators=(",", ":"))}


async def activate_buvid(buvid3: str, buvid4: str) -> bool:
    """Activate the fingerprint via ExClimbWuzhi. Returns True on code==0.

    Failure is non-fatal: log a warning and return False.
    """
    if not buvid3:
        return False
    settings = get_settings()
    headers = dict(_BILI_HEADERS)
    headers["Content-Type"] = "application/json"
    cookies = {"buvid3": buvid3}
    if buvid4:
        cookies["buvid4"] = buvid4
    try:
        async with httpx.AsyncClient(
            headers=headers, cookies=cookies, proxy=settings.network.proxy_url, timeout=20
        ) as client:
            resp = await client.post(
                BILI_EXCLIMB_URL, json=_build_exclimb_payload(buvid3)
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.warning("[bilibili] ExClimbWuzhi activation failed", exc_info=True)
        return False

    if data.get("code") != 0:
        logger.warning("[bilibili] ExClimbWuzhi non-zero code: %s", data.get("code"))
        return False
    logger.info("[bilibili] buvid activated via ExClimbWuzhi")
    return True


def _ticket_hexsign(ts: int) -> str:
    """HMAC-SHA256 hexsign over 'ts{ts}' with the web ticket key."""
    return hmac.new(
        _TICKET_HMAC_KEY, f"ts{ts}".encode(), hashlib.sha256
    ).hexdigest()


async def get_bili_ticket(bili_jct: str = "") -> tuple[str, int]:
    """Fetch a web bili_ticket. Returns (ticket, expires_at_unix).

    Returns ("", 0) on failure (fail loud, degrade).
    """
    settings = get_settings()
    ts = int(time.time())
    params = {
        "key_id": _TICKET_KEY_ID,
        "hexsign": _ticket_hexsign(ts),
        "context[ts]": str(ts),
        "csrf": bili_jct or "",
    }
    try:
        async with httpx.AsyncClient(
            headers=_BILI_HEADERS, proxy=settings.network.proxy_url, timeout=20
        ) as client:
            resp = await client.post(BILI_TICKET_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        logger.warning("[bilibili] bili_ticket fetch failed", exc_info=True)
        return "", 0

    if data.get("code") != 0:
        logger.warning("[bilibili] bili_ticket non-zero code: %s", data.get("code"))
        return "", 0
    d = data.get("data", {})
    ticket = str(d.get("ticket", ""))
    ttl = int(d.get("ttl", 0) or 0)
    created = int(d.get("created_at", ts) or ts)
    expires_at = created + ttl if ttl else 0
    return ticket, expires_at


async def refresh_fingerprint(bili_jct: str = "") -> Fingerprint:
    """Orchestrate a full fingerprint refresh and persist it.

    Steps (each degrades independently):
      1. finger/spi  -> buvid3, buvid4  (+ b_nut = seed timestamp)
      2. ExClimbWuzhi activation (best-effort)
      3. bili_ticket (cached by expiry)

    Reuses the existing cached buvid3/buvid4/b_nut if the fetch fails, and
    reuses a still-valid cached ticket instead of re-requesting one.
    """
    fp = load_fingerprint()

    buvid3, buvid4 = await fetch_fingerprint()
    if buvid3:
        # b_nut = unix seconds when buvid3 was (re)seeded.
        fp.buvid3 = buvid3
        fp.buvid4 = buvid4 or fp.buvid4
        fp.b_nut = str(int(time.time()))
        await activate_buvid(fp.buvid3, fp.buvid4)

    # Refresh ticket only if missing or within ~1h of expiry.
    if not fp.bili_ticket or fp.ticket_expires_at <= int(time.time()) + 3600:
        ticket, expires_at = await get_bili_ticket(bili_jct)
        if ticket:
            fp.bili_ticket = ticket
            fp.ticket_expires_at = expires_at

    save_fingerprint(fp)
    return fp
