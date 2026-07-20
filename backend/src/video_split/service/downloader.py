from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypedDict

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
    if settings.network.proxy_enabled and settings.network.http_proxy:
        return settings.network.http_proxy
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
    """Add YouTube-specific yt-dlp options (cookies + JS runtime for n-challenge)."""
    ck = _youtube_cookies_path()
    if ck:
        ydl_opts["cookiefile"] = ck
    ydl_opts["js_runtimes"] = {"node": {}}


_BILI_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _bilibili_headers(sessdata: str = "", bili_jct: str = "", buvid3: str = "") -> dict[str, str]:
    """Build HTTP headers for Bilibili requests.

    Always includes User-Agent and Referer (required to avoid 403/412).
    Appends cookies when credentials are provided.
    """
    headers: dict[str, str] = {
        "User-Agent": _BILI_UA,
        "Referer": "https://www.bilibili.com/",
    }
    if sessdata:
        headers["Cookie"] = f"SESSDATA={sessdata}; bili_jct={bili_jct}; buvid3={buvid3}"
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


async def _fetch_bilibili_subtitles_via_api(
    bvid: str, headers: dict[str, str]
) -> list[SubtitleEntry]:
    """Fetch Bilibili subtitles via official API."""
    view_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        resp = await client.get(view_url)
        resp.raise_for_status()
        data = resp.json()
    if data.get("code") != 0:
        return []

    cid = data["data"]["cid"]
    player_url = f"https://api.bilibili.com/x/player/v2?cid={cid}&bvid={bvid}"
    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        resp = await client.get(player_url)
        resp.raise_for_status()
        player_data = resp.json()
    if player_data.get("code") != 0:
        return []

    subtitles = player_data["data"]["subtitle"]["subtitles"]
    if not subtitles:
        return []

    for lang in ["zh-CN", "zh-Hans", "zh", "en", "ai-zh"]:
        for sub in subtitles:
            if sub.get("lan") == lang:
                sub_url = sub["subtitle_url"]
                if sub_url.startswith("//"):
                    sub_url = "https:" + sub_url
                async with httpx.AsyncClient(headers=headers, timeout=30) as client:
                    resp = await client.get(sub_url)
                    resp.raise_for_status()
                    content = resp.json()
                entries = []
                for item in content.get("body", []):
                    text = item.get("content", "").strip()
                    if text:
                        entries.append(
                            SubtitleEntry(
                                start=item.get("from", 0),
                                duration=item.get("to", 0) - item.get("from", 0),
                                text=text,
                            )
                        )
                return entries

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
    settings = get_settings()

    if platform == "bilibili":
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


async def fetch_youtube_subtitles(video_id: str) -> list[SubtitleEntry]:
    """Fetch YouTube subtitles via youtube-transcript-api (no auth needed)."""
    from youtube_transcript_api import YouTubeTranscriptApi

    logger.info("[subtitle] Fetching YouTube subtitles for %s", video_id)
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript = None
        for lang in ["zh-Hans", "zh", "zh-CN", "en", "ja"]:
            try:
                transcript = transcript_list.find_transcript([lang])
                break
            except Exception:
                continue
        if transcript is None:
            try:
                generated = transcript_list.find_generated_transcript(["zh-Hans", "zh", "en", "ja"])
                transcript = generated
            except Exception:
                pass
        if transcript is None:
            logger.info("[subtitle] No YouTube subtitles found for %s", video_id)
            return []
        data = transcript.fetch()
        entries = [
            SubtitleEntry(start=item["start"], duration=item["duration"], text=item["text"])
            for item in data
        ]
        logger.info("[subtitle] YouTube subtitles: %d entries for %s", len(entries), video_id)
        return entries
    except Exception:
        logger.exception("[subtitle] YouTube subtitle fetch failed for %s", video_id)
        return []


async def fetch_bilibili_subtitles(
    url: str, sessdata: str = "", bili_jct: str = "", buvid3: str = ""
) -> list[SubtitleEntry]:
    """Fetch Bilibili subtitles via API (bypasses 412 webpage blocks)."""
    _, bvid = detect_platform(url)
    headers = _bilibili_headers(sessdata, bili_jct, buvid3)
    logger.info("[subtitle] Bilibili via API: bvid=%s", bvid)
    try:
        return await _fetch_bilibili_subtitles_via_api(bvid, headers)
    except Exception:
        logger.exception("[subtitle] Bilibili API subtitle fetch failed for %s", url)
        return []


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

    settings = get_settings()
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
        ydl_opts["http_headers"] = _bilibili_headers(sessdata, bili_jct, buvid3)
        logger.info("[download] Bilibili headers attached (cookies=%s)", bool(sessdata))
    elif platform == "youtube":
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

    result = audio_files[0]
    if result.stat().st_size < MIN_AUDIO_BYTES:
        actual = result.stat().st_size
        raise RuntimeError(
            f"Audio download produced an invalid file ({actual} bytes). "
            "The source may be unavailable or region-restricted."
        )
    size_mb = result.stat().st_size / (1024 * 1024)
    logger.info("[download] Audio saved: %s (%.1f MB)", result, size_mb)
    return result


def generate_playback_url(platform: str, video_id: str, start_seconds: int) -> str:
    """Generate a URL that starts playback at the given timestamp."""
    if platform == "youtube":
        return f"https://www.youtube.com/watch?v={video_id}&t={start_seconds}"
    elif platform == "bilibili":
        return f"https://www.bilibili.com/video/{video_id}?t={start_seconds}"
    elif platform == "xiaoyuzhou":
        return f"https://www.xiaoyuzhoufm.com/episode/{video_id}?t={start_seconds}"
    return ""
