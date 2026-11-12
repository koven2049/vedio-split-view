"""Apple Podcasts (podcasts.apple.com) episode metadata and audio download.

Flow mirrors the xiaoyuzhou service: one proxied page fetch yields
everything —
- og:title / og:description / og:image for the episode card;
- embedded JSON: ``mediaEnclosures[0].streamUrl`` (the full-length audio
  direct link, usually mp3 on the publisher's CDN), an exact duration in
  seconds, and the show name;
- JSON-LD PodcastEpisode as a duration fallback (ISO-8601, minute-rounded).

Apple-exclusive paywalled episodes have no mediaEnclosures — surfaced as a
typed error instead of a confusing downstream download failure.
"""

from __future__ import annotations

import asyncio
import html as html_module
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

import httpx

from video_split.config import get_settings
from video_split.service.audio_integrity import (
    EARLY_PROBE_THRESHOLDS,
    TruncatedDownload,
    UndecodableAudio,
    early_probe,
    validate_decodable,
)
from video_split.service.downloader import (
    MIN_AUDIO_BYTES,
    DownloadProgress,
    VideoMeta,
    _proxy_for_platform,
)

logger = logging.getLogger(__name__)

_CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class ApplePodcastsError(RuntimeError):
    """Apple Podcasts scrape/download failure with a machine-readable ``code``.

    Codes:
        not_episode      — URL is not a single-episode link (?i= missing).
        page_changed     — page fetched but none of the known JSON anchors hit.
        exclusive        — episode has no downloadable audio (Apple-exclusive
                           / paywalled content).
        download_failed  — audio CDN request failed.
        audio_undecodable — the CDN serves a DRM'd stream: valid container
                       headers, ciphertext payloads (Task #71). Re-downloading
                       returns identical bytes; retrying is pointless.
        audio_truncated  — download incomplete (middle/tail undecodable);
                       auto-retried once by the downloader already.
    """

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _og_meta(html: str, prop: str) -> str:
    # The attribute value may contain the OTHER quote character (episode
    # titles love apostrophes: "Democrats' Midterm Blowout"), so each
    # pattern stops only at its own delimiter.
    patterns = (
        rf'<meta[^>]+property=["\']{re.escape(prop)}["\'][^>]*content="([^"]*)"',
        rf"<meta[^>]+content='([^']*)'[^>]+property=[\"']{re.escape(prop)}[\"']",
    )
    for pat in patterns:
        m = re.search(pat, html, re.IGNORECASE | re.DOTALL)
        if m:
            return html_module.unescape(m.group(1).strip())
    return ""


def _episode_audio_url(html: str) -> str:
    m = re.search(r'"mediaEnclosures":\[\{"streamUrl":"([^"]+)"', html)
    if not m:
        return ""
    return m.group(1).encode().decode("unicode_escape", errors="ignore")


def _exact_duration_seconds(html: str) -> float:
    """The page embeds a float duration in the episode JSON (e.g. 752.373)."""
    for m in re.finditer(r'"duration":([0-9]+(?:\.[0-9]+)?)\b', html):
        val = float(m.group(1))
        # Same page also carries ms-magnitude durations for other assets;
        # anything in the 10s..24h range with fraction precision is the
        # episode one. Prefer the first fractional hit.
        if 10.0 <= val <= 86400.0:
            frac = "." in m.group(1)
            if frac:
                return val
    # fall back to first plausible integer duration
    for m in re.finditer(r'"duration":([0-9]+(?:\.[0-9]+)?)\b', html):
        val = float(m.group(1))
        if 10.0 <= val <= 86400.0:
            return val
    return 0.0


def _ld_duration_seconds(html: str) -> int:
    """JSON-LD PodcastEpisode duration, ISO-8601 (minute-rounded fallback)."""
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for obj in items:
            if isinstance(obj, dict) and obj.get("@type") == "PodcastEpisode":
                dur = str(obj.get("duration", "") or "")
                if not dur.startswith("PT"):
                    continue
                total = 0
                for num, unit in re.findall(r"(\d+(?:\.\d+)?)([HMS])", dur):
                    n = float(num)
                    total += n * 3600 if unit == "H" else n * 60 if unit == "M" else n
                if total > 0:
                    return int(total)
    return 0


def _show_name(html: str) -> str:
    # og:description is "Podcast Episode · <show> · <date> · <length>"
    desc = _og_meta(html, "og:description")
    if desc:
        parts = [p.strip() for p in desc.split("·")]
        if len(parts) >= 2 and parts[1]:
            return parts[1][:128]
    m = re.search(r'"associatedPodcast"[^}]*?"name":"([^"]+)"', html)
    return m.group(1) if m else ""


async def extract_apple_podcasts_metadata(url: str) -> tuple[VideoMeta, str]:
    """Fetch the episode page and parse metadata; return (VideoMeta, audio_url).

    ``url`` must be a single-episode link; raises :class:`ApplePodcastsError`
    with typed codes otherwise.
    """
    if "podcasts.apple.com" not in url or "i=" not in url:
        raise ApplePodcastsError(
            "not_episode",
            "URL is not an Apple Podcasts single-episode link (?i= missing)",
        )
    show_id, ep_id = _platform_ids(url)
    if not show_id or not ep_id:
        raise ApplePodcastsError("not_episode", "Could not parse show/episode ids from URL")

    proxy = _proxy_for_platform("apple_podcasts")
    logger.info("[apple_podcasts] fetching episode page (proxy=%s)", bool(proxy))

    try:
        async with httpx.AsyncClient(
            proxy=proxy,
            timeout=60.0,
            follow_redirects=True,
            headers={"User-Agent": _CHROME_UA},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            page_html = resp.text
    except httpx.HTTPError:
        logger.exception("[apple_podcasts] page fetch failed for %s", url)
        raise

    title = _og_meta(page_html, "og:title")
    audio_url = _episode_audio_url(page_html)
    thumbnail_url = _og_meta(page_html, "og:image")
    duration_seconds = _exact_duration_seconds(page_html) or _ld_duration_seconds(page_html)
    uploader = _show_name(page_html)

    if not title or not audio_url:
        # Page loaded but anchors missed: either a structural change or an
        # Apple-exclusive episode (paywalled, no public audio enclosure).
        if title and '"offerName"' in page_html and "mediaEnclosures" not in page_html:
            raise ApplePodcastsError(
                "exclusive",
                "Episode has no downloadable audio (Apple-exclusive/paywalled)",
            )
        raise ApplePodcastsError(
            "page_changed",
            "Page structure changed (no og:title / mediaEnclosures found)",
        )

    meta = VideoMeta(
        url=url,
        platform="apple_podcasts",
        video_id=f"{show_id}:{ep_id}",
        title=title,
        duration_seconds=int(duration_seconds),
        thumbnail_url=thumbnail_url,
        upload_date="",
        uploader=uploader,
    )
    logger.info(
        "[apple_podcasts] metadata OK: title=%r duration=%ds show=%r",
        meta.title[:60],
        meta.duration_seconds,
        meta.uploader,
    )
    return meta, audio_url


def _platform_ids(url: str) -> tuple[str, str]:
    """Return (show_id, episode_id) from an episode URL, empty on no-match."""
    m = re.search(
        r"podcasts\.apple\.com/[a-z]{2}/podcast/[^/?#]+/id(\d+)(?:[?&]|.*[?&])i=(\d+)",
        url,
    )
    if m:
        return m.group(1), m.group(2)
    return "", ""


async def download_apple_podcasts_audio(
    audio_url: str,
    output_dir: Path,
    progress_callback: Callable[[DownloadProgress], None] | None = None,
    expected_duration_seconds: float | None = None,
) -> Path:
    """Stream-download the episode audio to ``output_dir/audio.<ext>``.

    Publisher CDNs serve plain mp3/m4a with content-length; downloads honor
    the platform proxy (same routing as the page fetch). DRM'd episodes
    (valid headers, ciphertext payloads — Task #71) are detected mid-stream
    at the shared early-probe thresholds and the finished file is validated
    at head/middle/tail windows; a truncated stream retries once.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ext = "m4a" if ".m4a" in audio_url.lower() else "mp3"
    out_path = output_dir / f"audio.{ext}"

    settings = get_settings()
    proxy = _proxy_for_platform("apple_podcasts")

    for attempt in (1, 2):
        logger.info("[apple_podcasts] Downloading audio to %s (proxy=%s, attempt %d)",
                    out_path, bool(proxy), attempt)
        try:
            dl_timeout = float(settings.network.download_timeout_seconds)
            async with httpx.AsyncClient(
                proxy=proxy,
                timeout=dl_timeout,
                follow_redirects=True,
                headers={"User-Agent": _CHROME_UA},
            ) as client, client.stream("GET", audio_url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length") or 0)
                downloaded = 0
                pending_probes = list(EARLY_PROBE_THRESHOLDS)
                with open(out_path, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            ratio = min(1.0, downloaded / total) if total > 0 else 0.0
                            progress_callback(
                                {
                                    "ratio": ratio,
                                    "downloaded_bytes": downloaded,
                                    "total_bytes": total,
                                }
                            )
                        if pending_probes and downloaded >= pending_probes[0]:
                            threshold = pending_probes.pop(0)
                            f.flush()
                            verdict = await asyncio.to_thread(early_probe, out_path)
                            if verdict == "encrypted":
                                logger.warning(
                                    "[apple_podcasts] early probe at %.1f MB (threshold %d MB): "
                                    "encrypted CDN content, aborting download",
                                    downloaded / (1024 * 1024), threshold // (1024 * 1024),
                                )
                                raise ApplePodcastsError(
                                    "audio_undecodable",
                                    f"该单集音频无法解码（下载 {downloaded // (1024 * 1024)} MB 时即检出"
                                    "内容被平台加密）。重新下载会得到相同字节，该单集暂不支持分析。",
                                )
                            # NB: "ok" must NOT skip later thresholds — on ffmpeg ≥9
                            # a 4 MB ciphertext prefix noise-decodes as a false "ok";
                            # the 64 MB re-probe is the one that convicts there.
        except ApplePodcastsError:
            out_path.unlink(missing_ok=True)
            raise
        except httpx.HTTPError as e:
            logger.exception("[apple_podcasts] audio download HTTP error: %s", audio_url[:80])
            out_path.unlink(missing_ok=True)
            raise ApplePodcastsError(
                "download_failed",
                f"Audio CDN request failed ({type(e).__name__})",
            ) from e
        except Exception:
            logger.exception("[apple_podcasts] audio download failed: %s", audio_url[:80])
            out_path.unlink(missing_ok=True)
            raise

        if not out_path.exists() or out_path.stat().st_size < MIN_AUDIO_BYTES:
            actual = out_path.stat().st_size if out_path.exists() else 0
            out_path.unlink(missing_ok=True)
            raise ApplePodcastsError(
                "download_failed",
                f"Audio download produced an invalid file ({actual} bytes).",
            )

        expected = float(expected_duration_seconds or 0.0)
        try:
            await asyncio.to_thread(validate_decodable, out_path, expected)
        except UndecodableAudio as e:
            raise ApplePodcastsError(
                "audio_undecodable",
                f"该单集音频无法解码（实测仅 {e.head_decoded:.1f}s 可读，标称 {expected:.0f}s）。"
                "内容疑似受平台加密保护，重新下载会得到相同字节，该单集暂不支持分析。",
            ) from e
        except TruncatedDownload as e:
            if attempt == 1:
                logger.warning("[apple_podcasts] Download looks truncated (%s) — retrying once", e)
                out_path.unlink(missing_ok=True)
                continue
            raise ApplePodcastsError(
                "audio_truncated",
                f"音频下载不完整（{e.start:.0f}s 处实测仅 {e.decoded:.1f}s 可解码，"
                f"标称 {expected:.0f}s），已自动重试一次仍失败，请重试分析。",
            ) from e

        size_mb = out_path.stat().st_size / (1024 * 1024)
        logger.info("[apple_podcasts] Audio saved: %s (%.1f MB)", out_path, size_mb)
        return out_path

    raise AssertionError("unreachable")  # pragma: no cover
