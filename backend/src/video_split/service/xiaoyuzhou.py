"""小宇宙 (Xiaoyuzhou) podcast episode metadata and audio download."""
from __future__ import annotations

import html as html_module
import json
import logging
import re
from pathlib import Path
from typing import Callable

import httpx

from video_split.config import get_settings
from video_split.service.downloader import MIN_AUDIO_BYTES, VideoMeta, detect_platform, normalize_url

logger = logging.getLogger(__name__)

_CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _og_meta(html: str, prop: str) -> str:
    """Parse Open Graph <meta property=\"...\" content=\"...\"/> (either attribute order)."""
    patterns = (
        rf'<meta[^>]+property=["\']{re.escape(prop)}["\'][^>]*content=["\']([^"\']*)["\']',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*property=["\']{re.escape(prop)}["\']',
    )
    for pat in patterns:
        m = re.search(pat, html, re.IGNORECASE | re.DOTALL)
        if m:
            return html_module.unescape(m.group(1).strip())
    return ""


def _iso8601_duration_to_seconds(duration: str) -> int:
    """Parse ISO-8601 duration like PT81M, PT1H30M5S to whole seconds."""
    if not duration:
        return 0
    d = duration.strip().upper()
    if not d.startswith("PT"):
        return 0
    rest = d[2:]
    total = 0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)([HMS])", rest):
        n = float(num)
        if unit == "H":
            total += int(n * 3600)
        elif unit == "M":
            total += int(n * 60)
        elif unit == "S":
            total += int(n)
    return total


def _upload_date_from_iso(iso_date: str) -> str:
    """2026-03-19T08:26:42.395Z -> 2026-03-19."""
    if not iso_date:
        return ""
    try:
        if "T" in iso_date:
            return iso_date.split("T", 1)[0]
        return iso_date[:10] if len(iso_date) >= 10 else iso_date
    except Exception:
        logger.debug("[xiaoyuzhou] Could not normalize datePublished: %r", iso_date)
        return ""


def _podcast_uploader_from_ld(obj: dict) -> str:
    """Best-effort show / channel name from JSON-LD PodcastEpisode."""
    part = obj.get("partOfSeries")
    if isinstance(part, dict):
        name = part.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    for key in ("author", "publisher", "creator"):
        val = obj.get(key)
        if isinstance(val, dict):
            n = val.get("name")
            if isinstance(n, str) and n.strip():
                return n.strip()
        elif isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _parse_podcast_ld_json(html: str) -> dict:
    """Return first JSON-LD object that looks like a podcast episode."""
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("[xiaoyuzhou] Skipping invalid JSON-LD block", exc_info=False)
            continue
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            types = obj.get("@type")
            type_names: set[str] = set()
            if isinstance(types, str):
                type_names.add(types)
            elif isinstance(types, list):
                type_names.update(str(t) for t in types if isinstance(t, str))
            if "PodcastEpisode" in type_names or "timeRequired" in obj or "datePublished" in obj:
                return obj
    return {}


async def extract_xiaoyuzhou_metadata(url: str) -> tuple[VideoMeta, str]:
    """Fetch episode HTML, parse OG tags and JSON-LD; return VideoMeta and direct m4a URL."""
    url = normalize_url(url.strip())
    platform, video_id = detect_platform(url)
    if platform != "xiaoyuzhou" or not video_id:
        logger.warning("[xiaoyuzhou] URL is not a 小宇宙 episode: %s", url)
        raise ValueError("Not a recognized 小宇宙 episode URL")

    settings = get_settings()
    proxy = None
    if settings.network.proxy_enabled and settings.network.http_proxy:
        proxy = settings.network.http_proxy

    headers = {"User-Agent": _CHROME_UA}
    try:
        async with httpx.AsyncClient(
            proxy=proxy,
            timeout=60.0,
            follow_redirects=True,
            headers=headers,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            page_html = resp.text
    except httpx.HTTPError:
        logger.exception("[xiaoyuzhou] HTTP fetch failed for %s", url)
        raise
    except Exception:
        logger.exception("[xiaoyuzhou] Unexpected error fetching %s", url)
        raise

    title = _og_meta(page_html, "og:title")
    audio_url = _og_meta(page_html, "og:audio")
    thumbnail_url = _og_meta(page_html, "og:image")
    og_description = _og_meta(page_html, "og:description")

    ld = _parse_podcast_ld_json(page_html)
    duration_seconds = _iso8601_duration_to_seconds(str(ld.get("timeRequired", "") or ""))
    if duration_seconds <= 0 and title:
        logger.warning("[xiaoyuzhou] duration missing or zero for %s", url)

    upload_date = _upload_date_from_iso(str(ld.get("datePublished", "") or ""))
    uploader = _podcast_uploader_from_ld(ld)
    if not uploader and og_description:
        uploader = og_description.strip().split("\n", 1)[0].strip()[:256]

    if ld.get("name") and isinstance(ld["name"], str) and not title:
        title = ld["name"].strip()

    if not audio_url:
        logger.error("[xiaoyuzhou] No og:audio in page: %s", url)
        raise RuntimeError("Could not find audio URL (og:audio) for this episode")

    meta = VideoMeta(
        url=url,
        platform="xiaoyuzhou",
        video_id=video_id,
        title=title or "Untitled episode",
        duration_seconds=duration_seconds,
        thumbnail_url=thumbnail_url,
        upload_date=upload_date,
        uploader=uploader,
    )
    logger.info(
        "[xiaoyuzhou] metadata OK: title=%r duration=%ds audio=%s",
        meta.title,
        meta.duration_seconds,
        audio_url[:80] + ("..." if len(audio_url) > 80 else ""),
    )
    return meta, audio_url


async def download_xiaoyuzhou_audio(
    audio_url: str,
    output_dir: Path,
    progress_callback: Callable[[float], None] | None = None,
) -> Path:
    """Stream-download episode m4a to ``output_dir/audio.m4a``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "audio.m4a"

    settings = get_settings()
    proxy = None
    if settings.network.proxy_enabled and settings.network.http_proxy:
        proxy = settings.network.http_proxy

    headers = {"User-Agent": _CHROME_UA}
    logger.info("[xiaoyuzhou] Downloading audio to %s", out_path)

    try:
        settings = get_settings()
        dl_timeout = float(settings.network.download_timeout_seconds)
        async with httpx.AsyncClient(
            proxy=proxy,
            timeout=dl_timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            async with client.stream("GET", audio_url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length") or 0)
                downloaded = 0
                with open(out_path, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total > 0:
                            progress_callback(min(1.0, downloaded / total))
    except httpx.HTTPError:
        logger.exception("[xiaoyuzhou] Audio download HTTP error: %s", audio_url)
        if out_path.exists():
            try:
                out_path.unlink()
            except OSError:
                pass
        raise
    except Exception:
        logger.exception("[xiaoyuzhou] Audio download failed: %s", audio_url)
        if out_path.exists():
            try:
                out_path.unlink()
            except OSError:
                pass
        raise

    if progress_callback:
        progress_callback(1.0)

    if not out_path.exists() or out_path.stat().st_size < MIN_AUDIO_BYTES:
        actual = out_path.stat().st_size if out_path.exists() else 0
        out_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Audio download produced an invalid file ({actual} bytes). "
            "The CDN link may be expired or the episode may be unavailable."
        )

    size_mb = out_path.stat().st_size / (1024 * 1024)
    logger.info("[xiaoyuzhou] Audio saved: %s (%.1f MB)", out_path, size_mb)
    return out_path
