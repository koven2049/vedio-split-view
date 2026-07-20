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
from video_split.service.downloader import MIN_AUDIO_BYTES, DownloadProgress, VideoMeta, detect_platform, normalize_url

logger = logging.getLogger(__name__)

_CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


# Heuristic markers that an episode page is behind a paywall / login wall.
# Matched against raw page text; case-insensitive substring search.
_PAID_PRIVATE_MARKERS = ("付费", "登录后可", "VIP", "会员")


class XiaoyuzhouError(RuntimeError):
    """小宇宙抓取/下载失败，带可机器读取的 ``code`` 供前端映射文案。

    Codes:
        cdn_expired   — 音频链接缺失/过期，重试会重抓页面拿新链接。
        paid_private  — 付费 / 私密内容，本服务仅支持公开单集。
        page_changed  — og 元数据与 JSON-LD 全 miss，疑似页面结构改版。
        not_episode   — URL 非 ``/episode/<id>``，前端理论上已拦，后端兜底。
    """

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _looks_paid_private(html: str, *, has_audio: bool) -> bool:
    """Heuristic: page text contains paywall markers AND audio is missing.

    Only trigger on missing audio — a page that has a usable audio URL but
    duration=0 (JSON-LD timeRequired format drift) should still attempt a
    normal download and surface cdn_expired on failure, rather than being
    misclassified as paid/private from shownotes text that mentions 付费/会员.
    """
    if not has_audio:
        return any(marker in html for marker in _PAID_PRIVATE_MARKERS)
    return False


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
    """Fetch episode HTML, parse OG tags and JSON-LD; return VideoMeta and direct m4a URL.

    Raises :class:`XiaoyuzhouError` with one of the typed ``code`` values
    (``not_episode`` / ``page_changed`` / ``paid_private`` / ``cdn_expired``)
    so the frontend can map a friendly, actionable message per case.
    """
    url = normalize_url(url.strip())
    platform, video_id = detect_platform(url)
    if platform != "xiaoyuzhou" or not video_id:
        logger.warning("[xiaoyuzhou] URL is not a 小宇宙 episode: %s", url)
        raise XiaoyuzhouError(
            "not_episode", "URL is not a 小宇宙 single-episode link (/episode/<id>)",
        )

    settings = get_settings()
    # 小宇宙是国内平台，无视全局 proxy 强制直连（见 downloader._proxy_for_platform）
    proxy = None

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

    # JSON-LD contentUrl is a fallback for the audio link.
    ld_content_url = ""
    raw_content_url = ld.get("contentUrl")
    if isinstance(raw_content_url, str) and raw_content_url.strip():
        ld_content_url = raw_content_url.strip()

    has_audio = bool(audio_url or ld_content_url)
    has_any_signal = bool(title) or bool(ld)

    # Classify failure before raising. Order matters:
    #   1. paid_private must precede cdn_expired — a paywalled page typically also
    #      lacks og:audio, and we want to surface the actionable "paid" reason,
    #      not the misleading "expired" one.
    #   2. cdn_expired: page parses but audio link is missing/expired — retry
    #      will re-fetch the page and likely get a fresh CDN URL.
    #   3. page_changed: og + JSON-LD both miss — structural change, retry
    #      unlikely to help until code is updated.
    if _looks_paid_private(page_html, has_audio=has_audio):
        logger.warning("[xiaoyuzhou] paywall/private markers found for %s", url)
        raise XiaoyuzhouError(
            "paid_private", "Episode is paywalled or private; only public episodes are supported",
        )

    if not has_audio:
        if has_any_signal:
            logger.error("[xiaoyuzhou] No audio link in otherwise valid page: %s", url)
            raise XiaoyuzhouError(
                "cdn_expired",
                "Audio link missing or expired; retry will re-fetch the page for a fresh CDN URL",
            )
        logger.error("[xiaoyuzhou] No og:title / PodcastEpisode JSON-LD in page: %s", url)
        raise XiaoyuzhouError(
            "page_changed",
            "Page structure changed (no og:title / PodcastEpisode JSON-LD found)",
        )

    if not audio_url and ld_content_url:
        audio_url = ld_content_url

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
    progress_callback: Callable[[DownloadProgress], None] | None = None,
) -> Path:
    """Stream-download episode m4a to ``output_dir/audio.m4a``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "audio.m4a"

    settings = get_settings()
    # 小宇宙是国内平台，无视全局 proxy 强制直连（见 downloader._proxy_for_platform）
    proxy = None

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
                        if progress_callback:
                            ratio = min(1.0, downloaded / total) if total > 0 else 0.0
                            progress_callback({
                                "ratio": ratio,
                                "downloaded_bytes": downloaded,
                                "total_bytes": total,
                            })
    except httpx.HTTPError as e:
        # 403/410/connect-failure on the audio CDN almost always means the
        # signed link has expired — re-running extract_xiaoyuzhou_metadata
        # fetches the page again and yields a fresh CDN URL.
        logger.exception("[xiaoyuzhou] Audio download HTTP error: %s", audio_url)
        if out_path.exists():
            try:
                out_path.unlink()
            except OSError:
                pass
        raise XiaoyuzhouError(
            "cdn_expired",
            f"Audio CDN request failed ({type(e).__name__}); retry will fetch a fresh link",
        ) from e
    except Exception:
        logger.exception("[xiaoyuzhou] Audio download failed: %s", audio_url)
        if out_path.exists():
            try:
                out_path.unlink()
            except OSError:
                pass
        raise

    if not out_path.exists() or out_path.stat().st_size < MIN_AUDIO_BYTES:
        actual = out_path.stat().st_size if out_path.exists() else 0
        out_path.unlink(missing_ok=True)
        raise XiaoyuzhouError(
            "cdn_expired",
            f"Audio download produced an invalid file ({actual} bytes). "
            "The CDN link may be expired or the episode may be unavailable.",
        )

    size_mb = out_path.stat().st_size / (1024 * 1024)
    logger.info("[xiaoyuzhou] Audio saved: %s (%.1f MB)", out_path, size_mb)
    return out_path
