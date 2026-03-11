"""Cloud ASR transcription — supports OpenAI Whisper API and Aliyun Fun-ASR."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from video_split.config import get_settings
from video_split.service.downloader import SubtitleEntry

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionProgress:
    """Rich progress event emitted during transcription."""
    step: str          # e.g. "splitting", "chunk_upload", "chunk_asr", "chunk_done"
    message: str
    chunk_index: int = 0
    total_chunks: int = 0
    extra: dict = field(default_factory=dict)


ProgressCB = Callable[[TranscriptionProgress], None]


@dataclass
class ASRUsage:
    """Usage info reported by the ASR API."""
    duration_seconds: float = 0.0
    model: str = ""


DASHSCOPE_HOSTS = ("dashscope.aliyuncs.com",)
FUNASR_MODELS = ("fun-asr", "fun-asr-mtl")


def _is_dashscope_funasr() -> bool:
    settings = get_settings()
    base = settings.transcription.base_url.lower()
    model = settings.transcription.model.lower()
    return any(h in base for h in DASHSCOPE_HOSTS) and any(
        model.startswith(m) for m in FUNASR_MODELS
    )


def get_audio_duration(audio_path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(audio_path)],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def split_audio(audio_path: Path, *, chunk_seconds: int | None = None) -> list[Path]:
    """Split audio into time-based chunks.

    If chunk_seconds is None, reads from config.transcription.chunk_duration_seconds.
    If the audio is shorter than one chunk, returns [audio_path] unchanged.
    """
    settings = get_settings()
    chunk_dur = chunk_seconds or settings.transcription.chunk_duration_seconds

    total_duration = get_audio_duration(audio_path)
    if total_duration <= chunk_dur:
        return [audio_path]

    num_chunks = math.ceil(total_duration / chunk_dur)
    chunk_dir = audio_path.parent / "chunks"
    chunk_dir.mkdir(exist_ok=True)

    chunks: list[Path] = []
    for i in range(num_chunks):
        start = i * chunk_dur
        out_path = chunk_dir / f"chunk_{i:03d}.mp3"
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(audio_path),
                "-ss", str(start), "-t", str(chunk_dur),
                "-acodec", "libmp3lame", "-ab", "128k",
                "-v", "quiet",
                str(out_path),
            ],
            check=True,
        )
        chunks.append(out_path)

    return chunks


# ---------------------------------------------------------------------------
# Transcript caching (per-chunk)
# ---------------------------------------------------------------------------

def _transcript_cache_path(audio_path: Path) -> Path:
    return audio_path.with_suffix(".transcript.json")


def _load_cached_transcript(audio_path: Path) -> list[SubtitleEntry] | None:
    cache = _transcript_cache_path(audio_path)
    if not cache.exists():
        return None
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        return [SubtitleEntry(**e) for e in data]
    except Exception:
        return None


def _save_transcript_cache(audio_path: Path, entries: list[SubtitleEntry]) -> None:
    cache = _transcript_cache_path(audio_path)
    data = [{"start": e.start, "duration": e.duration, "text": e.text} for e in entries]
    cache.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def transcribe_audio(
    audio_path: Path,
    progress_callback: ProgressCB | None = None,
) -> tuple[list[SubtitleEntry], ASRUsage]:
    """Transcribe audio file, returning entries and aggregated ASR usage.

    Both Whisper and Fun-ASR go through the same chunk-by-chunk flow.
    Each chunk is cached so retries / partial failures skip already-done work.
    """
    settings = get_settings()

    def _emit(p: TranscriptionProgress) -> None:
        if progress_callback:
            progress_callback(p)

    chunks = split_audio(audio_path)
    is_single = len(chunks) == 1 and chunks[0] == audio_path
    total = len(chunks)

    if total > 1:
        _emit(TranscriptionProgress(
            step="splitting", message=f"Audio split into {total} chunks",
            total_chunks=total,
        ))

    all_entries: list[SubtitleEntry] = []
    time_offset = 0.0
    total_asr_duration = 0.0

    for i, chunk_path in enumerate(chunks):
        _emit(TranscriptionProgress(
            step="chunk_start",
            message=f"Transcribing chunk {i + 1}/{total}...",
            chunk_index=i + 1, total_chunks=total,
        ))

        entries, chunk_dur = await transcribe_single_chunk(chunk_path, progress_callback=progress_callback, chunk_index=i + 1, total_chunks=total)
        total_asr_duration += chunk_dur

        for entry in entries:
            all_entries.append(
                SubtitleEntry(
                    start=entry.start + time_offset,
                    duration=entry.duration,
                    text=entry.text,
                )
            )
        if entries:
            last = entries[-1]
            time_offset += last.start + last.duration + 0.5

        _emit(TranscriptionProgress(
            step="chunk_done",
            message=f"Chunk {i + 1}/{total} done — {len(entries)} segments",
            chunk_index=i + 1, total_chunks=total,
            extra={"segments": len(entries)},
        ))

        if not is_single:
            chunk_path.unlink(missing_ok=True)

    asr_usage = ASRUsage(
        duration_seconds=total_asr_duration,
        model=settings.transcription.model,
    )
    return all_entries, asr_usage


async def transcribe_single_chunk(
    chunk_path: Path,
    *,
    progress_callback: ProgressCB | None = None,
    chunk_index: int = 1,
    total_chunks: int = 1,
) -> tuple[list[SubtitleEntry], float]:
    """Transcribe a single audio chunk with caching.

    Returns (entries, api_reported_duration_seconds).
    If cached, duration is 0 (no API call made).
    """
    def _emit(p: TranscriptionProgress) -> None:
        if progress_callback:
            progress_callback(p)

    cached = _load_cached_transcript(chunk_path)
    if cached is not None:
        logger.info("Cache hit for %s (%d segments)", chunk_path.name, len(cached))
        _emit(TranscriptionProgress(
            step="chunk_cached",
            message=f"Chunk {chunk_index}/{total_chunks} — using cached transcript ({len(cached)} segments)",
            chunk_index=chunk_index, total_chunks=total_chunks,
        ))
        return cached, 0.0

    if _is_dashscope_funasr():
        entries, dur = await _funasr_transcribe_single(chunk_path, progress_callback=progress_callback, chunk_index=chunk_index, total_chunks=total_chunks)
    else:
        _emit(TranscriptionProgress(
            step="chunk_upload",
            message=f"Chunk {chunk_index}/{total_chunks} — uploading to Whisper API...",
            chunk_index=chunk_index, total_chunks=total_chunks,
        ))
        entries, dur = await _whisper_transcribe_single(chunk_path)

    _save_transcript_cache(chunk_path, entries)
    logger.info("Transcribed %s → %d segments, duration=%.1fs (result cached)", chunk_path.name, len(entries), dur)
    return entries, dur


async def _whisper_transcribe_single(
    audio_path: Path,
    language_hint: str | None = None,
) -> tuple[list[SubtitleEntry], float]:
    settings = get_settings()
    base_url = settings.transcription.base_url.rstrip("/")
    url = f"{base_url}/audio/transcriptions"

    data: dict[str, str] = {
        "model": settings.transcription.model,
        "response_format": "verbose_json",
        "timestamp_granularities[]": "segment",
    }
    lang = language_hint or settings.transcription.language
    if lang:
        data["language"] = lang

    headers = {"Authorization": f"Bearer {settings.transcription.api_key}"}

    size_mb = audio_path.stat().st_size / (1024 * 1024)
    logger.info("[whisper] Uploading %s (%.1f MB) to %s", audio_path.name, size_mb, url)

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(300.0),
        ) as client:
            with open(audio_path, "rb") as f:
                resp = await client.post(
                    url, headers=headers, data=data,
                    files={"file": (audio_path.name, f, "audio/mpeg")},
                )
            resp.raise_for_status()
            result = resp.json()
    except httpx.HTTPStatusError as e:
        logger.error("[whisper] HTTP %d: %s", e.response.status_code, e.response.text[:500])
        raise
    except Exception:
        logger.exception("[whisper] Request failed for %s", audio_path.name)
        raise

    api_duration = float(result.get("duration", 0))
    entries: list[SubtitleEntry] = []
    for seg in result.get("segments", []):
        entries.append(
            SubtitleEntry(
                start=float(seg.get("start", 0)),
                duration=float(seg.get("end", 0)) - float(seg.get("start", 0)),
                text=seg.get("text", "").strip(),
            )
        )
    logger.info("[whisper] %s → %d segments, duration=%.1fs", audio_path.name, len(entries), api_duration)
    return entries, api_duration


# ---------------------------------------------------------------------------
# Fun-ASR (DashScope async task + OSS signed URL)
# ---------------------------------------------------------------------------

_FUNASR_SUBMIT_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"
_FUNASR_TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks"
_FUNASR_MAX_POLL = 300
_FUNASR_POLL_INTERVAL = 2


async def _funasr_transcribe_single(
    audio_path: Path,
    *,
    progress_callback: ProgressCB | None = None,
    chunk_index: int = 1,
    total_chunks: int = 1,
) -> tuple[list[SubtitleEntry], float]:
    from video_split.service.oss_helper import upload_and_sign, delete_object

    def _emit(p: TranscriptionProgress) -> None:
        if progress_callback:
            progress_callback(p)

    settings = get_settings()
    if not settings.oss.enabled:
        raise RuntimeError(
            "Fun-ASR requires publicly accessible URLs. "
            "Please configure oss.* in config/app.yaml to enable OSS upload."
        )

    size_mb = audio_path.stat().st_size / (1024 * 1024)
    _emit(TranscriptionProgress(
        step="chunk_upload",
        message=f"Chunk {chunk_index}/{total_chunks} — uploading to OSS ({size_mb:.1f} MB)...",
        chunk_index=chunk_index, total_chunks=total_chunks,
    ))

    logger.info("[funasr] Uploading %s (%.1f MB) to OSS", audio_path.name, size_mb)
    object_key, signed_url = await asyncio.get_event_loop().run_in_executor(
        None, upload_and_sign, audio_path
    )
    logger.info("[funasr] OSS upload done: key=%s", object_key)

    _emit(TranscriptionProgress(
        step="chunk_asr",
        message=f"Chunk {chunk_index}/{total_chunks} — submitted to Fun-ASR, waiting for result...",
        chunk_index=chunk_index, total_chunks=total_chunks,
    ))

    try:
        entries, dur = await _funasr_call(signed_url, progress_callback=progress_callback, chunk_index=chunk_index, total_chunks=total_chunks)
    finally:
        await asyncio.get_event_loop().run_in_executor(None, delete_object, object_key)
        logger.info("[funasr] OSS object deleted: %s", object_key)

    return entries, dur


async def _funasr_call(
    audio_url: str,
    *,
    progress_callback: ProgressCB | None = None,
    chunk_index: int = 1,
    total_chunks: int = 1,
) -> tuple[list[SubtitleEntry], float]:
    def _emit(p: TranscriptionProgress) -> None:
        if progress_callback:
            progress_callback(p)

    settings = get_settings()
    api_key = settings.transcription.api_key
    model = settings.transcription.model
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    lang = settings.transcription.language
    params: dict = {}
    if lang:
        params["language_hints"] = [lang]

    body: dict = {
        "model": model,
        "input": {"file_urls": [audio_url]},
    }
    if params:
        body["parameters"] = params

    logger.info("[funasr] Submitting task: model=%s", model)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        try:
            resp = await client.post(_FUNASR_SUBMIT_URL, headers=headers, json=body)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error("[funasr] Submit HTTP %d: %s", e.response.status_code, e.response.text[:500])
            raise
        task_id = resp.json()["output"]["task_id"]
        logger.info("[funasr] Task submitted: task_id=%s", task_id)

        poll_headers = {"Authorization": f"Bearer {api_key}"}
        start_time = time.monotonic()

        while True:
            elapsed = time.monotonic() - start_time
            if elapsed > _FUNASR_MAX_POLL:
                logger.error("[funasr] Task %s timed out after %ds", task_id, _FUNASR_MAX_POLL)
                raise TimeoutError(f"Fun-ASR task {task_id} timed out after {_FUNASR_MAX_POLL}s")

            await asyncio.sleep(_FUNASR_POLL_INTERVAL)

            poll_resp = await client.get(
                f"{_FUNASR_TASK_URL}/{task_id}", headers=poll_headers
            )
            poll_resp.raise_for_status()
            full_resp = poll_resp.json()
            output = full_resp.get("output", {})
            status = output.get("task_status", "UNKNOWN")

            elapsed_int = int(elapsed)
            _emit(TranscriptionProgress(
                step="chunk_asr_polling",
                message=f"Chunk {chunk_index}/{total_chunks} — ASR processing ({elapsed_int}s)...",
                chunk_index=chunk_index, total_chunks=total_chunks,
                extra={"elapsed_seconds": elapsed_int, "asr_status": status},
            ))

            if status == "SUCCEEDED":
                usage = full_resp.get("usage", {})
                api_duration = float(usage.get("duration", 0))
                logger.info("[funasr] Task %s succeeded (%.1fs), billed_duration=%.1fs", task_id, elapsed, api_duration)
                entries = await _parse_funasr_results(client, output)
                return entries, api_duration
            if status == "FAILED":
                code = output.get("code", "")
                msg = output.get("message", "Unknown error")
                logger.error("[funasr] Task %s failed: %s — %s", task_id, code, msg)
                raise RuntimeError(f"Fun-ASR failed: {code} — {msg}")


async def _parse_funasr_results(
    client: httpx.AsyncClient, output: dict
) -> list[SubtitleEntry]:
    entries: list[SubtitleEntry] = []
    for result in output.get("results", []):
        tr_url = result.get("transcription_url")
        if not tr_url or result.get("subtask_status") != "SUCCEEDED":
            continue
        tr_resp = await client.get(tr_url)
        tr_resp.raise_for_status()
        tr_data = tr_resp.json()

        for transcript in tr_data.get("transcripts", []):
            for sent in transcript.get("sentences", []):
                begin_s = sent["begin_time"] / 1000.0
                end_s = sent["end_time"] / 1000.0
                text = sent.get("text", "").strip()
                if text:
                    entries.append(SubtitleEntry(
                        start=begin_s,
                        duration=end_s - begin_s,
                        text=text,
                    ))
    return entries
