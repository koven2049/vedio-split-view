"""Import Bilibili cookies from a Netscape cookies.txt into the admin
credential + device-fingerprint cache.

Alternative to the QR-login flow: when you already have a browser-exported
cookies.txt (SESSDATA / bili_jct / buvid3 / buvid4 / b_nut / bili_ticket),
this seeds the same state QR login would have produced, so the downloader
sends a full, logged-in cookie set (best mitigation for -352 on data-center IPs).

Usage (inside the backend environment):
    python -m video_split.import_bili_cookies /path/to/www.bilibili.com_cookies.txt \
        [--config config/app.yaml] [--admin-username admin]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from video_split.config import set_config_path
from video_split.database import _get_session_factory, init_db
from video_split.models import BilibiliCredential, User
from video_split.service.bilibili_auth import Fingerprint, save_fingerprint


def _parse_netscape_cookies(path: str) -> dict[str, str]:
    """Parse a Netscape cookies.txt into {name: value}. Ignores comments/blank."""
    cookies: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            name, value = parts[5], parts[6]
            cookies[name] = value
    return cookies


async def _import(cookie_path: str, admin_username: str) -> int:
    cookies = _parse_netscape_cookies(cookie_path)
    sessdata = cookies.get("SESSDATA", "")
    bili_jct = cookies.get("bili_jct", "")
    buvid3 = cookies.get("buvid3", "")
    if not sessdata or not bili_jct:
        print("[ERROR] cookies.txt missing SESSDATA / bili_jct — not a logged-in export", file=sys.stderr)
        return 2

    # SESSDATA expiry: url-encoded "hash,expiry,..." — second field is unix seconds.
    expires_at = None
    try:
        raw = sessdata.replace("%2C", ",")
        expiry_ts = int(raw.split(",")[1])
        expires_at = datetime.fromtimestamp(expiry_ts, tz=timezone.utc)
    except (IndexError, ValueError):
        pass

    await init_db()
    factory = _get_session_factory()
    async with factory() as db:
        admin = (
            await db.execute(select(User).where(User.username == admin_username))
        ).scalar_one_or_none()
        if admin is None:
            print(f"[ERROR] admin user '{admin_username}' not found", file=sys.stderr)
            return 3

        cred = (
            await db.execute(
                select(BilibiliCredential).where(BilibiliCredential.user_id == admin.id)
            )
        ).scalar_one_or_none()
        if cred is None:
            cred = BilibiliCredential(user_id=admin.id)
            db.add(cred)
        cred.sessdata = sessdata
        cred.bili_jct = bili_jct
        cred.buvid3 = buvid3
        cred.bilibili_username = cookies.get("DedeUserID", "")
        cred.expires_at = expires_at
        await db.commit()

    # Fingerprint cache (shared with the downloader, which has no DB access).
    ticket_expires = 0
    try:
        ticket_expires = int(cookies.get("bili_ticket_expires", "0"))
    except ValueError:
        pass
    save_fingerprint(Fingerprint(
        buvid3=buvid3,
        buvid4=cookies.get("buvid4", ""),
        b_nut=cookies.get("b_nut", ""),
        bili_ticket=cookies.get("bili_ticket", ""),
        ticket_expires_at=ticket_expires,
    ))

    exp = expires_at.date().isoformat() if expires_at else "unknown"
    print(f"[OK] Imported Bilibili cookies for '{admin_username}' "
          f"(DedeUserID={cookies.get('DedeUserID', '?')}, SESSDATA expires {exp}); "
          f"fingerprint cached (buvid4={'yes' if cookies.get('buvid4') else 'no'}, "
          f"bili_ticket={'yes' if cookies.get('bili_ticket') else 'no'}).")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Import Bilibili cookies.txt into admin credential + fingerprint cache")
    ap.add_argument("cookie_file", help="Path to Netscape cookies.txt exported from a logged-in browser")
    ap.add_argument("--config", default="config/app.yaml", help="Path to app.yaml (default: config/app.yaml)")
    ap.add_argument("--admin-username", default="admin", help="Admin username to attach credentials to (default: admin)")
    args = ap.parse_args()

    set_config_path(args.config)
    rc = asyncio.run(_import(args.cookie_file, args.admin_username))
    sys.exit(rc)


if __name__ == "__main__":
    main()
