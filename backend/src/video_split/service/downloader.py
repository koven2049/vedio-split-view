from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from video_split.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class VideoMeta:
    url: str
    platform: str
    video_id: str
    title: str
    duration_seconds: int
    thumbnail_url: str
    upload_date: str = ""


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

    return "unknown", ""


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
    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "format": "bestaudio/best",
    }
    if settings.network.proxy_enabled and settings.network.http_proxy:
        ydl_opts["proxy"] = settings.network.http_proxy

    if platform == "bilibili":
        ydl_opts["http_headers"] = _bilibili_headers(sessdata, bili_jct, buvid3)
        logger.info("[metadata] Bilibili headers attached (cookies=%s)", bool(sessdata))
    elif platform == "youtube":
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
        proxy = None
        if settings.network.proxy_enabled and settings.network.http_proxy:
            proxy = settings.network.http_proxy

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
    """Fetch Bilibili subtitles via yt-dlp with optional cookies."""
    import yt_dlp

    settings = get_settings()
    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
    }
    if settings.network.proxy_enabled and settings.network.http_proxy:
        ydl_opts["proxy"] = settings.network.http_proxy

    ydl_opts["http_headers"] = _bilibili_headers(sessdata, bili_jct, buvid3)
    logger.info("[subtitle] Bilibili subtitle fetch (cookies=%s): %s", bool(sessdata), url)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if info is None:
            logger.warning("[subtitle] yt-dlp returned None for %s", url)
            return []

        subtitles_data = info.get("subtitles", {})
        auto_subs = info.get("automatic_captions", {})
        all_subs = {**auto_subs, **subtitles_data}
        logger.info("[subtitle] Available subtitle langs: %s", list(all_subs.keys()))

        for lang in ["zh-Hans", "zh", "zh-CN", "en", "ai_zh"]:
            if lang in all_subs:
                for fmt in all_subs[lang]:
                    if fmt.get("ext") == "json3" or "json" in fmt.get("ext", ""):
                        import httpx

                        logger.info("[subtitle] Downloading subtitle: lang=%s ext=%s", lang, fmt.get("ext"))
                        async with httpx.AsyncClient() as client:
                            resp = await client.get(fmt["url"])
                            data = resp.json()
                            events = data.get("events", data.get("body", []))
                            entries = []
                            for ev in events:
                                start = ev.get("tStartMs", ev.get("from", 0)) / 1000
                                dur = ev.get("dDurationMs", ev.get("to", 0) - ev.get("from", 0))
                                dur = dur / 1000 if dur > 100 else dur
                                text_parts = ev.get("segs", [{"utf8": ev.get("content", "")}])
                                text = "".join(s.get("utf8", "") for s in text_parts).strip()
                                if text:
                                    entries.append(SubtitleEntry(start=start, duration=dur, text=text))
                            logger.info("[subtitle] Bilibili subtitles: %d entries", len(entries))
                            return entries
        logger.info("[subtitle] No suitable Bilibili subtitle format found for %s", url)
        return []
    except Exception:
        logger.exception("[subtitle] Bilibili subtitle fetch failed for %s", url)
        return []


async def download_audio(
    url: str,
    output_dir: Path,
    progress_callback: Callable[[float], None] | None = None,
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
            if total > 0:
                progress_callback(downloaded / total)

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
    if settings.network.proxy_enabled and settings.network.http_proxy:
        ydl_opts["proxy"] = settings.network.http_proxy

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
    size_mb = result.stat().st_size / (1024 * 1024)
    logger.info("[download] Audio saved: %s (%.1f MB)", result, size_mb)
    return result


def generate_playback_url(platform: str, video_id: str, start_seconds: int) -> str:
    """Generate a URL that starts playback at the given timestamp."""
    if platform == "youtube":
        return f"https://www.youtube.com/watch?v={video_id}&t={start_seconds}"
    elif platform == "bilibili":
        return f"https://www.bilibili.com/video/{video_id}?t={start_seconds}"
    return ""
