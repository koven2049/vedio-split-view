"""Download-time audio integrity checks shared by the podcast platforms.

Podcast CDNs serve plain files whose container duration is an estimate
(size÷bitrate for raw MP3), so only a real decode tells whether the bytes are
usable. Platform downloaders use these helpers to (a) abort an undecodable
download mid-stream, (b) validate the finished file at head/middle/tail
windows, and (c) retry a genuinely truncated download once.

History: Tasks #69/#70/#71 (2026-11) were first diagnosed here as "DRM'd
episodes with ciphertext payloads". That was wrong — the files were healthy
MP3s with embedded ID3 cover art, which ffmpeg 9 lets stop the decode at
~0.15s once the file exceeds ~50 MB (hence the "only ≥64 MB prefixes fail"
observation). The fix is ``-vn`` in transcriber's ffmpeg calls; verify with a
second decoder (macOS ``afinfo``, ffmpeg 7) before blaming the platform.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from video_split.service.transcriber import probe_decoded_seconds

logger = logging.getLogger(__name__)

# A probe window "passes" when this much of the requested 8s actually decodes
# (byte-estimate seeking on VBR MP3 can land a couple seconds off target).
PROBE_PASS_SECONDS = 5.0

# Stream-early detection: decode-probe the head each time a threshold below
# is crossed; an "undecodable" verdict aborts the download instead of
# dragging the whole file over the network. A traffic optimization only —
# correctness rests on the post-download validation of the complete file.
EARLY_PROBE_THRESHOLDS = (4 * 1024 * 1024, 64 * 1024 * 1024)


def container_opens(path: Path) -> bool:
    """Whether ffprobe can identify the container at all (format name only)."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=format_name",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=False,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def early_probe(path: Path) -> str:
    """Classify a partial download: ``ok`` / ``undecodable`` / ``unknown``.

    Runs one ffmpeg head-window decode: a prefix that decodes ≥5s is "ok"; a
    prefix that opens as audio yet decodes ≈0s is "undecodable"; a prefix
    that can't be judged yet (still inside a giant cover-art ID3 tag, or a
    container that won't even open) is "unknown" and defers to the
    post-download validation on the complete file.
    """
    decoded = probe_decoded_seconds(path, start=0.0, window=8.0)
    if decoded >= PROBE_PASS_SECONDS:
        return "ok"
    with open(path, "rb") as f:
        head = f.read(10)
    if head[:3] == b"ID3":
        tag_size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
        if 10 + tag_size >= path.stat().st_size:
            return "unknown"
    if not container_opens(path):
        return "unknown"
    return "undecodable"


class TruncatedDownload(Exception):
    """Head decodes but middle/tail windows don't — transient, retry once."""

    def __init__(self, start: float, decoded: float):
        self.start = start
        self.decoded = decoded
        super().__init__(f"window at {start:.0f}s decoded only {decoded:.1f}s")


def probe_start_offsets(expected_duration: float) -> list[float]:
    """Decode-probe window starts: head, plus middle/tail for long episodes.

    The tail window keeps a 90s margin behind the claimed end so the
    minute-rounded JSON-LD fallback (up to 59s short) never pushes a probe
    past the real EOF. Windows whose coverage the downstream split tolerance
    (max(60s, 2%)) already forgives are simply omitted.
    """
    offsets = [0.0]
    if expected_duration >= 60:
        offsets.append(expected_duration / 2)
    if expected_duration >= 180:
        offsets.append(max(60.0, expected_duration - 90))
    return offsets


def validate_decodable(path: Path, expected_duration: float) -> None:
    """Refuse files whose bytes do not decode, split by cause.

    Raises :class:`TruncatedDownload` when head decodes but middle/tail
    windows fail (network truncation — caller retries once), and returns
    silently otherwise — callers map the undecodable case themselves (their
    typed error classes differ per platform), so this only measures.
    For the head-window failure the caller should treat the file as
    undecodable (deterministic, retry is pointless).
    """
    for start in probe_start_offsets(expected_duration):
        decoded = probe_decoded_seconds(path, start=start, window=8.0)
        if decoded >= PROBE_PASS_SECONDS:
            continue
        if start == 0.0:
            raise UndecodableAudio(head_decoded=decoded)
        raise TruncatedDownload(start, decoded)
    logger.info(
        "[audio_integrity] decode probe OK: %s (windows at %s, expected %.0fs)",
        path.name, [round(s) for s in probe_start_offsets(expected_duration)], expected_duration,
    )


class UndecodableAudio(Exception):
    """Container opens as audio yet the head decodes ≈0s — re-downloading
    yields identical bytes, retrying is pointless."""

    def __init__(self, head_decoded: float):
        self.head_decoded = head_decoded
        super().__init__(f"head window decoded only {head_decoded:.1f}s")
