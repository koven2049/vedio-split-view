from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypedDict
from urllib.parse import parse_qs, urlparse

import httpx

from video_split.config import get_settings

logger = logging.getLogger(__name__)

MIN_AUDIO_BYTES = 10_240  # 10 KB — reject audio files smaller than this


@dataclass
class VideoMeta:
    url: str
    platform: str
    video_id: str
    title: str
    duration_seconds: int
    thumbnail_url: str
    upload_date: str = ""
    uploader: str = ""


@dataclass
class SubtitleEntry:
    start: float
    duration: float
    text: str


@dataclass
class DownloadResult:
    audio_path: Path | None = None
    subtitles: list[SubtitleEntry] = field(default_factory=list)
    subtitle_source: str = ""


def normalize_url(url: str) -> str:
    """Normalize video URL to the canonical form expected by yt-dlp."""
    url = url.strip()
    url = re.sub(r"https?://bilibili\.com/", "https://www.bilibili.com/", url)
    url = re.sub(r"https?://xiaoyuzhoufm\.com/", "https://www.xiaoyuzhoufm.com/", url)
    return url


def detect_platform(url: str) -> tuple[str, str]:
    """Return (platform, video_id) from URL."""
    yt_patterns = [
        r"(?:youtube\.com/(?:watch\?v=|shorts/|embed/|live/)|youtu\.be/)([a-zA-Z0-9_-]{11})",
    ]
    for pat in yt_patterns:
        m = re.search(pat, url)
        if m:
            return "youtube", m.group(1)

    bili_patterns = [
        r"bilibili\.com/video/(BV[a-zA-Z0-9]+)",
        r"b23\.tv/([a-zA-Z0-9]+)",
    ]
    for pat in bili_patterns:
        m = re.search(pat, url)
        if m:
            return "bilibili", m.group(1)

    xiaoyuzhou_patterns = [
        r"xiaoyuzhoufm\.com/episode/([a-f0-9]{24})",
    ]
    for pat in xiaoyuzhou_patterns:
        m = re.search(pat, url)
        if m:
            return "xiaoyuzhou", m.group(1)

    return "unknown", ""


# Domestic platforms always connect directly, bypassing the global proxy even
# when proxy_enabled is on (proxy is intended for YouTube etc.).
_DIRECT_PLATFORMS = {"bilibili", "xiaoyuzhou"}


def _proxy_for_platform(platform: str) -> str | None:
    """Return proxy URL for this platform, or None to connect directly.

    bilibili / xiaoyuzhou are domestic China platforms — always direct.
    """
    if platform in _DIRECT_PLATFORMS:
        return None
    settings = get_settings()
    if settings.network.proxy_enabled:
        return settings.network.proxy_url
    return None


class DownloadProgress(TypedDict):
    """Progress payload passed to ``download_audio`` / ``download_xiaoyuzhou_audio``
    callbacks. ``total_bytes == 0`` means the server did not report content-length."""

    ratio: float
    downloaded_bytes: int
    total_bytes: int


def _youtube_cookies_path() -> str | None:
    """Return a writable copy of the YouTube cookies file.

    yt-dlp writes back updated cookies, so we copy from the read-only
    config mount to a writable temp location.
    """
    settings = get_settings()
    path_str = settings.network.youtube_cookies_file
    if not path_str:
        return None
    src = Path(path_str)
    if not src.is_absolute():
        src = Path("config") / src
    if not src.exists():
        logger.warning("[yt-dlp] YouTube cookies file not found: %s", src)
        return None

    import shutil
    writable = Path(settings.storage.temp_dir) / "_cookies" / src.name
    writable.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, writable)
    return str(writable)


def _apply_youtube_opts(ydl_opts: dict[str, Any]) -> None:
    """Add YouTube-specific yt-dlp options (optional cookies + Deno for n-challenge).

    Deno is yt-dlp's default JS runtime (Node needs >=22; Debian apt ships 20).
    Cookies stay optional for age-restricted / members-only videos — they are
    not the 2026 fix for bot-check. Do not pair them with a PO Token plugin.
    """
    ck = _youtube_cookies_path()
    if ck:
        ydl_opts["cookiefile"] = ck
    ydl_opts["js_runtimes"] = {"deno": {}}
    # web_embedded / android currently yield GVS URLs without a PO Token.
    # Default web/android_vr clients list formats then 403 on the media fetch.
    youtube_args = ydl_opts.setdefault("extractor_args", {}).setdefault("youtube", {})
    youtube_args.setdefault("player_client", ["web_embedded", "android"])


_BILI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Seconds to pause between successive Bilibili requests (both yt-dlp and the
# sequential API calls) — conservative default to avoid -352 frequency
# risk-control on data-center IPs.
_BILI_SLEEP_REQUESTS = 1.0

_YT_SUB_LANGS = ["zh-Hans", "zh", "zh-CN", "en", "ja", "ko", "es"]


async def _ensure_bilibili_fingerprint(bili_jct: str = "") -> None:
    """Best-effort device-fingerprint refresh before Bilibili API calls."""
    try:
        from video_split.service.bilibili_auth import ensure_fingerprint

        await ensure_fingerprint(bili_jct)
    except Exception:
        logger.warning("[bilibili] fingerprint ensure failed", exc_info=True)


def _parse_youtube_json3(data: dict[str, Any]) -> list[SubtitleEntry]:
    """Parse YouTube json3 timedtext into SubtitleEntry rows."""
    entries: list[SubtitleEntry] = []
    for event in data.get("events") or []:
        text = "".join(seg.get("utf8", "") for seg in event.get("segs") or []).strip()
        if not text:
            continue
        entries.append(
            SubtitleEntry(
                start=event.get("tStartMs", 0) / 1000.0,
                duration=event.get("dDurationMs", 0) / 1000.0,
                text=text,
            )
        )
    return entries


def _pick_youtube_subtitle_url(tracks: list[dict[str, Any]]) -> str | None:
    """Prefer json3 (easy parse) over vtt/srv3."""
    if not tracks:
        return None
    for ext in ("json3", "srv3", "vtt"):
        for track in tracks:
            if track.get("ext") == ext and track.get("url"):
                return str(track["url"])
    return tracks[0].get("url") or None


def _bilibili_headers(sessdata: str = "", bili_jct: str = "", buvid3: str = "") -> dict[str, str]:
    """Build HTTP headers for Bilibili requests.

    Always includes User-Agent and Referer (required to avoid 403/412).
    Appends login cookies when provided, plus any cached device-fingerprint
    cookies (buvid3/buvid4/b_nut/bili_ticket) to reduce -352 risk-control on
    data-center IPs. The fingerprint is best-effort: if none is cached the
    Cookie header simply omits those fields.
    """
    headers: dict[str, str] = {
        "User-Agent": _BILI_UA,
        "Referer": "https://www.bilibili.com/",
    }

    cookies: dict[str, str] = {}
    # Device fingerprint (works even without a logged-in session).
    try:
        from video_split.service.bilibili_auth import load_fingerprint

        cookies.update(load_fingerprint().as_cookie_dict())
    except Exception:
        logger.warning("[bilibili] fingerprint load failed", exc_info=True)

    if sessdata:
        cookies["SESSDATA"] = sessdata
        cookies["bili_jct"] = bili_jct
    # An explicit buvid3 (e.g. from QR login) takes precedence over the cached one.
    if buvid3:
        cookies["buvid3"] = buvid3

    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return headers


async def _fetch_bilibili_metadata_via_api(
    bvid: str, headers: dict[str, str]
) -> VideoMeta:
    """Fetch Bilibili metadata via official API (bypasses 412 webpage blocks)."""
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Bilibili API error: {data.get('message', 'unknown')}")

    video = data["data"]
    pubdate = video.get("pubdate", 0)
    upload_date = datetime.fromtimestamp(pubdate).strftime("%Y-%m-%d") if pubdate else ""

    return VideoMeta(
        url=f"https://www.bilibili.com/video/{bvid}",
        platform="bilibili",
        video_id=bvid,
        title=video.get("title") or "",
        duration_seconds=video.get("duration") or 0,
        thumbnail_url=video.get("pic") or "",
        upload_date=upload_date,
        uploader=video.get("owner", {}).get("name") or "",
    )


async def _fetch_bilibili_view_once(
    bvid: str, headers: dict[str, str]
) -> dict:
    """Single /view API call reused by metadata + subtitle fetchers."""
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Bilibili API error: {data.get('message', 'unknown')}")
    return data


async def _fetch_bilibili_metadata_from_view(view_data: dict, bvid: str) -> VideoMeta:
    """Extract VideoMeta from /view response."""
    video = view_data["data"]
    pubdate = video.get("pubdate", 0)
    upload_date = datetime.fromtimestamp(pubdate).strftime("%Y-%m-%d") if pubdate else ""

    return VideoMeta(
        url=f"https://www.bilibili.com/video/{bvid}",
        platform="bilibili",
        video_id=bvid,
        title=video.get("title") or "",
        duration_seconds=video.get("duration") or 0,
        thumbnail_url=video.get("pic") or "",
        upload_date=upload_date,
        uploader=video.get("owner", {}).get("name") or "",
    )



def _bilibili_page_number(url: str) -> int:
    """Return requested Bilibili page number, defaulting to the first page."""
    try:
        page = int(parse_qs(urlparse(url).query).get("p", ["1"])[0])
    except (TypeError, ValueError):
        return 1
    return max(page, 1)


def _bilibili_cid(data: dict, page_number: int) -> int | None:
    """Choose CID for requested multi-part page, with page 1 fallback."""
    video = data.get("data", {})
    if page_number == 1 and video.get("cid"):
        return video["cid"]
    pages = video.get("pages") or []
    for page in pages:
        if page.get("page") == page_number and page.get("cid"):
            return page["cid"]
    return video.get("cid")


async def _fetch_bilibili_subtitles_via_api(
    bvid: str, headers: dict[str, str], page_number: int = 1, view_data: dict | None = None
) -> list[SubtitleEntry]:
    """Fetch subtitles for requested Bilibili multi-part page via official API.

    Accepts pre-fetched `view_data` to avoid duplicate /view calls when metadata
    was already fetched.
    """
    if view_data is None:
        try:
            view_data = await _fetch_bilibili_view_once(bvid, headers)
        except Exception:
            return []

    cid = _bilibili_cid(view_data, page_number)
    if not cid:
        return []
    import asyncio
    await asyncio.sleep(_BILI_SLEEP_REQUESTS)
    player_url = f"https://api.bilibili.com/x/player/v2?cid={cid}&bvid={bvid}"
    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        resp = await client.get(player_url)
        resp.raise_for_status()
        player_data = resp.json()
    if player_data.get("code") != 0:
        return []

    subtitles = (player_data.get("data") or {}).get("subtitle", {}).get("subtitles", [])
    if not subtitles:
        return []

    for lang in ["zh-CN", "zh-Hans", "zh", "en", "ai-zh"]:
        for sub in subtitles:
            if sub.get("lan") == lang:
                sub_url = sub.get("subtitle_url", "")
                if sub_url.startswith("//"):
                    sub_url = "https:" + sub_url
                if not sub_url:
                    continue
                async with httpx.AsyncClient(headers=headers, timeout=30) as client:
                    resp = await client.get(sub_url)
                    resp.raise_for_status()
                    content = resp.json()
                return [
                    SubtitleEntry(
                        start=item.get("from", 0),
                        duration=item.get("to", 0) - item.get("from", 0),
                        text=item.get("content", "").strip(),
                    )
                    for item in content.get("body", [])
                    if item.get("content", "").strip()
                ]

    return []


async def extract_metadata(
    url: str,
    *,
    sessdata: str = "",
    bili_jct: str = "",
    buvid3: str = "",
) -> VideoMeta:
    """Extract video metadata without downloading."""
    import yt_dlp

    platform, vid = detect_platform(url)

    if platform == "bilibili":
        await _ensure_bilibili_fingerprint(bili_jct)
        headers = _bilibili_headers(sessdata, bili_jct, buvid3)
        logger.info("[metadata] Bilibili via API: bvid=%s", vid)
        try:
            return await _fetch_bilibili_metadata_via_api(vid, headers)
        except Exception:
            logger.warning("[metadata] Bilibili API failed, falling back to yt-dlp: %s", url)

    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "format": "bestaudio/best",
    }
    ydl_opts["proxy"] = _proxy_for_platform(platform) or ""

    if platform == "youtube":
        _apply_youtube_opts(ydl_opts)

    logger.info("[metadata] Extracting metadata: platform=%s vid=%s url=%s", platform, vid, url)
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        logger.exception("[metadata] yt-dlp extract_info failed for %s", url)
        raise

    if info is None:
        raise RuntimeError("Failed to extract video metadata")

    raw_date = info.get("upload_date", "") or ""
    upload_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}" if len(raw_date) == 8 else raw_date

    meta = VideoMeta(
        url=url,
        platform=platform,
        video_id=vid or info.get("id", ""),
        title=info.get("title", ""),
        duration_seconds=int(info.get("duration", 0)),
        thumbnail_url=info.get("thumbnail", ""),
        upload_date=upload_date,
        uploader=info.get("uploader", "") or info.get("channel", "") or "",
    )
    logger.info(
        "[metadata] OK: title=%r duration=%ds platform=%s",
        meta.title, meta.duration_seconds, meta.platform,
    )
    return meta


async def download_thumbnail(meta: VideoMeta) -> str:
    """Download remote thumbnail to local storage. Returns the API-relative path.

    Saved to data/thumbnails/{platform}_{video_id}.jpg so the file persists
    beyond task cleanup.  Returns empty string on failure (non-fatal).
    """
    if not meta.thumbnail_url:
        return ""

    settings = get_settings()
    thumb_dir = Path(settings.storage.temp_dir).parent / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)

    ext = "jpg"
    if ".png" in meta.thumbnail_url.lower():
        ext = "png"
    elif ".webp" in meta.thumbnail_url.lower():
        ext = "webp"
    filename = f"{meta.platform}_{meta.video_id}.{ext}"
    local_path = thumb_dir / filename

    if local_path.exists() and local_path.stat().st_size > 0:
        logger.info("[thumbnail] Using cached: %s", local_path)
        return f"/api/thumbnails/{filename}"

    import httpx

    try:
        proxy = _proxy_for_platform(meta.platform)

        headers = {}
        if meta.platform == "bilibili":
            headers["Referer"] = "https://www.bilibili.com"

        async with httpx.AsyncClient(proxy=proxy, timeout=30, follow_redirects=True) as client:
            resp = await client.get(meta.thumbnail_url, headers=headers)
            resp.raise_for_status()
            local_path.write_bytes(resp.content)
            logger.info("[thumbnail] Downloaded: %s (%.1f KB)", filename, len(resp.content) / 1024)
            return f"/api/thumbnails/{filename}"
    except Exception:
        logger.warning("[thumbnail] Failed to download %s", meta.thumbnail_url, exc_info=True)
        return meta.thumbnail_url


def _pick_youtube_subtitle_file(files: list[Path], video_id: str) -> Path | None:
    """Prefer a written json3 file in the same language order as _YT_SUB_LANGS."""
    by_name = {path.name: path for path in files}
    for lang in _YT_SUB_LANGS:
        match = by_name.get(f"{video_id}.{lang}.json3")
        if match:
            return match
    return files[0] if files else None


async def _fetch_youtube_subtitles_via_ytdlp(video_id: str) -> list[SubtitleEntry]:
    """Fetch captions through yt-dlp so Deno / proxy / cookies stay in one path."""
    import tempfile

    import yt_dlp

    url = f"https://www.youtube.com/watch?v={video_id}"
    with tempfile.TemporaryDirectory() as tmp:
        ydl_opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": list(_YT_SUB_LANGS),
            "subtitlesformat": "json3",
            "ignoreerrors": True,
            "outtmpl": str(Path(tmp) / "%(id)s.%(ext)s"),
        }
        ydl_opts["proxy"] = _proxy_for_platform("youtube") or ""
        _apply_youtube_opts(ydl_opts)

        logger.info("[subtitle] YouTube via yt-dlp: %s", video_id)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        chosen = _pick_youtube_subtitle_file(
            sorted(Path(tmp).glob(f"{video_id}*.json3")), video_id
        )
        if not chosen:
            logger.info("[subtitle] yt-dlp wrote no json3 captions for %s", video_id)
            return []
        return _parse_youtube_json3(json.loads(chosen.read_text(encoding="utf-8")))


async def _fetch_youtube_subtitles_legacy(video_id: str) -> list[SubtitleEntry]:
    """Fallback when yt-dlp cannot list caption tracks."""
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api.proxies import GenericProxyConfig

    proxy_url = _proxy_for_platform("youtube")
    configs: list[GenericProxyConfig | None] = []
    if proxy_url:
        configs.append(GenericProxyConfig(http_url=proxy_url, https_url=proxy_url))
    configs.append(None)

    for index, proxy_config in enumerate(configs):
        try:
            kwargs: dict[str, Any] = {}
            if proxy_config:
                kwargs["proxy_config"] = proxy_config
            transcript = YouTubeTranscriptApi(**kwargs).fetch(video_id, languages=_YT_SUB_LANGS)
            return [
                SubtitleEntry(start=snip.start, duration=snip.duration, text=snip.text)
                for snip in transcript.snippets
                if snip.text.strip()
            ]
        except Exception:
            if index + 1 < len(configs):
                logger.warning("[subtitle] YouTube legacy fetch via proxy failed; retrying direct")
            else:
                logger.exception("[subtitle] YouTube legacy subtitle fetch failed for %s", video_id)
    return []


async def fetch_youtube_subtitles(video_id: str) -> list[SubtitleEntry]:
    """Fetch YouTube subtitles via yt-dlp, falling back to youtube-transcript-api."""
    logger.info("[subtitle] Fetching YouTube subtitles for %s", video_id)
    try:
        entries = await _fetch_youtube_subtitles_via_ytdlp(video_id)
        if entries:
            logger.info("[subtitle] YouTube yt-dlp subtitles: %d entries for %s", len(entries), video_id)
            return entries
    except Exception:
        logger.warning("[subtitle] yt-dlp caption path failed for %s; trying legacy API", video_id, exc_info=True)

    entries = await _fetch_youtube_subtitles_legacy(video_id)
    if entries:
        logger.info("[subtitle] YouTube legacy subtitles: %d entries for %s", len(entries), video_id)
    return entries


async def fetch_bilibili_subtitles(
    url: str, sessdata: str = "", bili_jct: str = "", buvid3: str = ""
) -> list[SubtitleEntry]:
    """Fetch subtitles for requested Bilibili page via API."""
    _, bvid = detect_platform(url)
    page_number = _bilibili_page_number(url)
    await _ensure_bilibili_fingerprint(bili_jct)
    headers = _bilibili_headers(sessdata, bili_jct, buvid3)
    logger.info("[subtitle] Bilibili via API: bvid=%s page=%d", bvid, page_number)
    try:
        if page_number == 1:
            return await _fetch_bilibili_subtitles_via_api(bvid, headers)
        return await _fetch_bilibili_subtitles_via_api(bvid, headers, page_number)
    except Exception:
        logger.exception("[subtitle] Bilibili API subtitle fetch failed for %s", url)
        return []


async def fetch_bilibili_metadata_and_subtitles(
    url: str, sessdata: str = "", bili_jct: str = "", buvid3: str = ""
) -> tuple[VideoMeta | None, list[SubtitleEntry]]:
    """Fetch Bilibili metadata + subtitles with one shared /view call.

    Returns (metadata, subtitles). Either may be None/empty on failure, but
    both are attempted from the same view response when successful.
    """
    _, bvid = detect_platform(url)
    page_number = _bilibili_page_number(url)
    await _ensure_bilibili_fingerprint(bili_jct)
    headers = _bilibili_headers(sessdata, bili_jct, buvid3)
    logger.info("[bilibili] fetching metadata + subtitles: bvid=%s page=%d", bvid, page_number)

    try:
        view_data = await _fetch_bilibili_view_once(bvid, headers)
        meta = await _fetch_bilibili_metadata_from_view(view_data, bvid)
        subs = await _fetch_bilibili_subtitles_via_api(bvid, headers, page_number, view_data)
        return meta, subs
    except Exception:
        logger.exception("[bilibili] combined fetch failed for %s", url)
        return None, []


def _pick_bilibili_audio_url(play: dict[str, Any]) -> str:
    """Choose the highest-bandwidth DASH audio URL, with durl fallback."""
    dash = play.get("dash") or {}
    audios = list(dash.get("audio") or [])
    if audios:
        best = max(audios, key=lambda item: item.get("bandwidth") or 0)
        url = best.get("baseUrl") or best.get("base_url") or ""
        if url:
            return str(url)
    durl = play.get("durl") or []
    if durl and durl[0].get("url"):
        return str(durl[0]["url"])
    raise RuntimeError("Bilibili playurl returned no audio stream")


def _ffmpeg_to_mp3(src: Path, dest: Path) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-codec:a", "libmp3lame", "-b:a", "128k", str(dest)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").splitlines()[-6:])
        raise RuntimeError(f"ffmpeg mp3 convert failed (exit {proc.returncode}): {tail}")


def _validate_audio_file(result: Path) -> Path:
    if result.stat().st_size < MIN_AUDIO_BYTES:
        actual = result.stat().st_size
        raise RuntimeError(
            f"Audio download produced an invalid file ({actual} bytes). "
            "The source may be unavailable or region-restricted."
        )
    size_mb = result.stat().st_size / (1024 * 1024)
    logger.info("[download] Audio saved: %s (%.1f MB)", result, size_mb)
    return result


async def _download_bilibili_audio_via_api(
    url: str,
    output_dir: Path,
    progress_callback: Callable[[DownloadProgress], None] | None = None,
    *,
    sessdata: str = "",
    bili_jct: str = "",
    buvid3: str = "",
) -> Path:
    """Download Bilibili audio via playurl API (webpage scrape gets 412)."""
    import asyncio

    await _ensure_bilibili_fingerprint(bili_jct)
    headers = _bilibili_headers(sessdata, bili_jct, buvid3)
    _, bvid = detect_platform(url)
    page_number = _bilibili_page_number(url)
    view = await _fetch_bilibili_view_once(bvid, headers)
    cid = _bilibili_cid(view, page_number)
    if not cid:
        raise RuntimeError(f"Bilibili playurl: no cid for {bvid} page={page_number}")

    await asyncio.sleep(_BILI_SLEEP_REQUESTS)
    play_api = (
        f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}"
        "&fnval=16&fnver=0&fourk=1"
    )
    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        resp = await client.get(play_api)
        resp.raise_for_status()
        payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"Bilibili playurl error: {payload.get('message', 'unknown')}")

    audio_url = _pick_bilibili_audio_url(payload.get("data") or {})
    raw_path = output_dir / "audio.m4a"
    mp3_path = output_dir / "audio.mp3"
    download_headers = dict(headers)
    download_headers["Referer"] = "https://www.bilibili.com"

    logger.info("[download] Bilibili via playurl API: bvid=%s cid=%s", bvid, cid)
    async with httpx.AsyncClient(
        headers=download_headers, timeout=120, follow_redirects=True
    ) as client:
        async with client.stream("GET", audio_url) as stream:
            stream.raise_for_status()
            total = int(stream.headers.get("content-length") or 0)
            downloaded = 0
            with raw_path.open("wb") as fh:
                async for chunk in stream.aiter_bytes():
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        ratio = downloaded / total if total > 0 else 0.0
                        progress_callback({
                            "ratio": ratio,
                            "downloaded_bytes": downloaded,
                            "total_bytes": total,
                        })

    _ffmpeg_to_mp3(raw_path, mp3_path)
    raw_path.unlink(missing_ok=True)
    return _validate_audio_file(mp3_path)


async def download_audio(
    url: str,
    output_dir: Path,
    progress_callback: Callable[[DownloadProgress], None] | None = None,
    *,
    sessdata: str = "",
    bili_jct: str = "",
    buvid3: str = "",
) -> Path:
    """Download audio-only stream. Returns path to audio file."""
    import yt_dlp

    output_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(output_dir / "audio.%(ext)s")

    platform, vid = detect_platform(url)
    logger.info("[download] Starting audio download: platform=%s vid=%s dir=%s", platform, vid, output_dir)

    def _progress_hook(d: dict[str, Any]) -> None:
        if progress_callback and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            downloaded = d.get("downloaded_bytes", 0)
            ratio = downloaded / total if total > 0 else 0.0
            progress_callback({
                "ratio": ratio,
                "downloaded_bytes": int(downloaded),
                "total_bytes": int(total),
            })

    ydl_opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [_progress_hook],
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }
        ],
    }
    ydl_opts["proxy"] = _proxy_for_platform(platform) or ""

    if platform == "bilibili":
        return await _download_bilibili_audio_via_api(
            url,
            output_dir,
            progress_callback,
            sessdata=sessdata,
            bili_jct=bili_jct,
            buvid3=buvid3,
        )
    if platform == "youtube":
        _apply_youtube_opts(ydl_opts)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception:
        logger.exception("[download] yt-dlp download failed for %s", url)
        raise

    audio_files = list(output_dir.glob("audio.*"))
    if not audio_files:
        raise RuntimeError("Audio download failed: no output file found")
    return _validate_audio_file(audio_files[0])


def generate_playback_url(platform: str, video_id: str, start_seconds: int) -> str:
    """Generate a URL that starts playback at the given timestamp."""
    if platform == "youtube":
        return f"https://www.youtube.com/watch?v={video_id}&t={start_seconds}"
    elif platform == "bilibili":
        return f"https://www.bilibili.com/video/{video_id}?t={start_seconds}"
    elif platform == "xiaoyuzhou":
        return f"https://www.xiaoyuzhoufm.com/episode/{video_id}?t={start_seconds}"
    return ""
