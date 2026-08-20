from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from video_split.config import get_settings
from sqlalchemy import select

from video_split.models import BilibiliCredential, Segment, Tag, Task, User, Video, video_tags
from video_split.schemas import ProgressEvent
from video_split.service.error_text import describe_error
from video_split.service.analyzer import analyze_transcript
from video_split.service.downloader import (
    MIN_AUDIO_BYTES,
    SubtitleEntry,
    detect_platform,
    download_audio,
    download_thumbnail,
    extract_metadata,
    fetch_bilibili_metadata_and_subtitles,
    fetch_youtube_subtitles,
)
from video_split.service.xiaoyuzhou import XiaoyuzhouError, download_xiaoyuzhou_audio, extract_xiaoyuzhou_metadata
from video_split.service.task_manager import (
    cleanup_task_files,
    get_task_temp_dir,
    update_task_status,
)
from video_split.service.transcriber import ASRUsage, TranscriptionProgress, get_audio_duration, transcribe_audio

logger = logging.getLogger(__name__)

_PLATFORM_TAG_MAP = {
    "youtube": ("YouTube", "#ff0000"),
    "bilibili": ("Bilibili", "#00a1d6"),
    "xiaoyuzhou": ("小宇宙", "#7c3aed"),
}


async def _attach_platform_tag(db: AsyncSession, video: Video) -> None:
    """Automatically attach a platform tag (YouTube / Bilibili) to a video."""
    tag_info = _PLATFORM_TAG_MAP.get(video.platform)
    if not tag_info:
        return
    name, color = tag_info
    result = await db.execute(select(Tag).where(Tag.name == name))
    tag = result.scalar_one_or_none()
    if tag is None:
        tag = Tag(name=name, color=color)
        db.add(tag)
        await db.flush()
    await db.execute(video_tags.insert().values(video_id=video.id, tag_id=tag.id))


class AnalysisCancelled(Exception):
    pass


class DurationLimitExceeded(ValueError):
    """Video/podcast exceeds the hard max-duration limit.

    Deterministic — retrying the same URL can never succeed, so the frontend
    should show the reason without offering a retry button
    (error_code=duration_exceeded).
    """


def _transcription_progress_pct(tp: TranscriptionProgress) -> int:
    """Map transcription sub-step to an overall progress percentage (56-84 range)."""
    base, span = 56, 28  # transcription occupies progress 56..84
    if tp.total_chunks <= 1:
        step_map = {"splitting": 0.0, "chunk_start": 0.05, "chunk_upload": 0.1, "chunk_cached": 0.4,
                     "chunk_asr": 0.2, "chunk_asr_polling": 0.5, "chunk_done": 0.95}
        return base + int(span * step_map.get(tp.step, 0.5))

    chunk_frac = (tp.chunk_index - 1) / tp.total_chunks
    within_chunk = {"chunk_start": 0.0, "chunk_upload": 0.15, "chunk_cached": 0.8,
                    "chunk_asr": 0.3, "chunk_asr_polling": 0.6, "chunk_done": 1.0}
    frac = chunk_frac + within_chunk.get(tp.step, 0.5) / tp.total_chunks
    return base + int(span * min(frac, 0.99))


def _cred_kwargs(cred: BilibiliCredential | None) -> dict[str, str]:
    """Extract credential fields as keyword arguments for downloader functions."""
    if not cred:
        return {}
    return {"sessdata": cred.sessdata, "bili_jct": cred.bili_jct, "buvid3": cred.buvid3}


def get_user_limits(user) -> dict[str, int]:
    """Return effective limits for a user, merging per-user overrides with global config."""
    settings = get_settings()
    prefs: dict = {}
    try:
        prefs = json.loads(user.preferences_json or "{}")
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    return {
        "max_duration_seconds": prefs.get("max_duration_seconds") or settings.video.max_duration_seconds,
        "confirm_threshold_seconds": prefs.get("confirm_threshold_seconds") or settings.video.confirm_threshold_seconds,
        "podcast_confirm_threshold_seconds": (
            prefs.get("podcast_confirm_threshold_seconds")
            or settings.video.podcast_confirm_threshold_seconds
        ),
        "max_concurrent": prefs.get("max_concurrent_analyses") or settings.storage.max_pending_tasks_per_user,
    }


async def accumulate_usage(db: AsyncSession, user_id: int, usage_data: dict) -> None:
    """Add analysis usage to the user's cumulative stats (persisted in DB)."""
    from sqlalchemy import select as sa_select
    result = await db.execute(sa_select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return
    try:
        stats = json.loads(user.usage_stats_json or "{}")
    except (json.JSONDecodeError, TypeError):
        stats = {}

    asr_model = usage_data.get("asr_model", "")
    if asr_model:
        asr = stats.setdefault("asr", {}).setdefault(asr_model, {"total_seconds": 0, "requests": 0})
        asr["total_seconds"] += usage_data.get("asr_duration_seconds", 0)
        asr["requests"] += 1

    llm_model = usage_data.get("llm_model", "")
    if llm_model:
        llm = stats.setdefault("llm", {}).setdefault(llm_model, {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "requests": 0,
        })
        llm["prompt_tokens"] += usage_data.get("llm_prompt_tokens", 0)
        llm["completion_tokens"] += usage_data.get("llm_completion_tokens", 0)
        llm["total_tokens"] += usage_data.get("llm_total_tokens", 0)
        llm["requests"] += 1

    user.usage_stats_json = json.dumps(stats, ensure_ascii=False)
    await db.commit()


def _reconcile_duration(
    meta: "VideoMeta", subtitles: list[SubtitleEntry], task_id: int | None = None
) -> int:
    """Widen a truncated platform duration to cover the actual transcript.

    WHY: 小宇宙 publishes JSON-LD ``timeRequired`` rounded down to whole
    minutes (``PT14M`` for a 14:31 episode), so ``duration_seconds`` can be up
    to 59s short. Segment bounds and the progress bar are built from it, which
    silently cuts off the tail of every episode. The transcript's own end is
    ground truth: audio that produced words is audio that exists.

    Only ever grows the value — a transcript ending early (trailing silence)
    is normal and must not shrink the video.
    """
    if not subtitles:
        return meta.duration_seconds
    transcript_end = max((s.start + s.duration for s in subtitles), default=0.0)
    corrected = max(meta.duration_seconds, int(round(transcript_end)))
    if corrected != meta.duration_seconds:
        logger.info(
            "[analysis] Task %s — duration %ds → %ds (transcript runs %.0fs)",
            task_id, meta.duration_seconds, corrected, transcript_end,
        )
    return corrected


async def _save_analysis_results(
    db: AsyncSession,
    task: Task,
    meta: "VideoMeta",
    analysis: "AnalysisResult",
    subtitles: list[SubtitleEntry],
    asr_usage: "ASRUsage",
) -> tuple[Video, dict]:
    """Persist analysis results to DB and return (video, usage_data)."""
    llm_u = analysis.llm_usage
    usage_data = {
        "asr_duration_seconds": asr_usage.duration_seconds,
        "asr_model": asr_usage.model,
        "llm_prompt_tokens": llm_u.prompt_tokens,
        "llm_completion_tokens": llm_u.completion_tokens,
        "llm_total_tokens": llm_u.total_tokens,
        "llm_model": llm_u.model,
    }
    transcript_text = "\n".join(
        f"[{int(s.start) // 60:02d}:{int(s.start) % 60:02d}] {s.text}" for s in subtitles
    )
    subtitle_data = json.dumps(
        [{"start": s.start, "duration": s.duration, "text": s.text} for s in subtitles],
        ensure_ascii=False,
    )
    video = Video(
        user_id=task.user_id, url=meta.url, platform=meta.platform, video_id=meta.video_id,
        title=meta.title, thumbnail_url=meta.thumbnail_url, upload_date=meta.upload_date,
        duration_seconds=meta.duration_seconds, summary=analysis.summary, summary_en=analysis.summary_en,
        essence=analysis.essence,
        raw_transcript=transcript_text, subtitle_json=subtitle_data,
        usage_json=json.dumps(usage_data, ensure_ascii=False),
    )
    db.add(video)
    await db.flush()
    await _attach_platform_tag(db, video)
    for seg in analysis.segments:
        db.add(Segment(
            video_id=video.id, segment_index=seg.index, title=seg.title, title_en=seg.title_en,
            summary=seg.summary, summary_en=seg.summary_en,
            start_seconds=seg.start_seconds, end_seconds=seg.end_seconds,
        ))
    await update_task_status(db, task, "completed")
    await cleanup_task_files(task)
    await db.commit()
    await accumulate_usage(db, task.user_id, usage_data)
    return video, usage_data


async def run_analysis(
    db: AsyncSession,
    task: Task,
    cancel_event: asyncio.Event,
    bilibili_cred: BilibiliCredential | None = None,
    confirm_event: asyncio.Event | None = None,
    user_limits: dict[str, int] | None = None,
) -> AsyncGenerator[ProgressEvent, None]:
    """Run the full analysis pipeline, yielding progress events."""
    settings = get_settings()
    cred_kw = _cred_kwargs(bilibili_cred)

    subtitles: list[SubtitleEntry] = []

    def _check_cancelled() -> None:
        if cancel_event.is_set():
            raise AnalysisCancelled()

    try:
        # Stage 1: Extract metadata
        yield ProgressEvent(stage="metadata", progress=5, message="Extracting video metadata...")
        logger.info("[analysis] Task #%d — extracting metadata for %s", task.id, task.url)
        platform_hint, _ = detect_platform(task.url)
        if platform_hint == "xiaoyuzhou":
            meta, audio_url = await extract_xiaoyuzhou_metadata(task.url)
        else:
            meta = await extract_metadata(task.url, **cred_kw)
            audio_url = ""
        _check_cancelled()

        local_thumb = await download_thumbnail(meta)
        if local_thumb:
            meta.thumbnail_url = local_thumb

        await update_task_status(db, task, "downloading", video_title=meta.title)

        duration = meta.duration_seconds
        limits = user_limits or {}
        max_dur = limits.get("max_duration_seconds") or settings.video.max_duration_seconds
        if duration > max_dur:
            raise DurationLimitExceeded(
                f"Video is {duration // 3600}h{(duration % 3600) // 60}m, "
                f"exceeding the {max_dur // 3600}h{(max_dur % 3600) // 60}m limit."
            )

        threshold = limits.get("confirm_threshold_seconds") or settings.video.confirm_threshold_seconds
        if meta.platform == "xiaoyuzhou":
            threshold = (
                limits.get("podcast_confirm_threshold_seconds")
                or settings.video.podcast_confirm_threshold_seconds
            )
        if confirm_event and duration > threshold:
            duration_str_confirm = f"{duration // 60}m{duration % 60:02d}s"
            if meta.platform == "xiaoyuzhou":
                confirm_message = (
                    f"播客时长 {duration_str_confirm}（超过 {threshold // 60} 分钟阈值），"
                    f"转写耗时较长，确认开始？"
                )
            else:
                confirm_message = (
                    f"Video duration {duration_str_confirm} exceeds {threshold // 60} min threshold. "
                    f"Please confirm to continue."
                )
            yield ProgressEvent(
                stage="confirm_required", progress=10,
                message=confirm_message,
                detail={"task_id": task.id, "title": meta.title, "duration_seconds": duration,
                        "thumbnail_url": meta.thumbnail_url, "platform": meta.platform,
                        "threshold_seconds": threshold},
            )
            logger.info("[analysis] Task #%d — awaiting user confirmation (duration=%ds, threshold=%ds)",
                        task.id, duration, threshold)
            try:
                await asyncio.wait_for(confirm_event.wait(), timeout=300)
            except asyncio.TimeoutError:
                raise AnalysisCancelled()
            _check_cancelled()
            logger.info("[analysis] Task #%d — user confirmed, proceeding", task.id)

        duration_str = f"{meta.duration_seconds // 60}m{meta.duration_seconds % 60:02d}s"
        yield ProgressEvent(
            stage="metadata", progress=10, message=f"Video: {meta.title}",
            detail={
                "title": meta.title,
                "platform": meta.platform,
                "duration": duration_str,
                "duration_seconds": meta.duration_seconds,
                "thumbnail_url": meta.thumbnail_url,
                "uploader": meta.uploader,
                "upload_date": meta.upload_date,
            },
        )
        _check_cancelled()

        # Stage 2: Try subtitles
        transcript_method = ""
        asr_usage = ASRUsage()

        if meta.platform == "youtube":
            yield ProgressEvent(
                stage="subtitle_check", progress=12,
                message="Checking YouTube subtitles (no auth needed)...",
            )
            subtitles = await fetch_youtube_subtitles(meta.video_id)
        elif meta.platform == "bilibili" and bilibili_cred:
            yield ProgressEvent(
                stage="subtitle_check", progress=12,
                message="Checking Bilibili subtitles (using saved credentials)...",
            )
            # Combined metadata+subtitle fetch with single /view API call
            _, subtitles = await fetch_bilibili_metadata_and_subtitles(
                task.url, **cred_kw,
            )
        elif meta.platform == "bilibili":
            yield ProgressEvent(
                stage="subtitle_check", progress=12,
                message="Bilibili account not connected, will use audio transcription.",
                detail={"hint": "bilibili_not_connected"},
            )
        elif meta.platform == "xiaoyuzhou":
            yield ProgressEvent(
                stage="subtitle_check", progress=14,
                message="Podcast — no subtitles available, will use audio transcription.",
                detail={"method": "whisper", "reason": "Podcast platform (no subtitles)"},
            )

        _check_cancelled()

        if subtitles:
            transcript_method = "subtitle"
            logger.info("[analysis] Task #%d — subtitles found: %d entries", task.id, len(subtitles))
            yield ProgressEvent(
                stage="subtitle_check", progress=60,
                message=f"Subtitles found ({len(subtitles)} entries). Skipping audio download.",
                detail={"method": "subtitle", "entry_count": len(subtitles)},
            )
        else:
            if meta.platform == "youtube":
                no_sub_reason = "No subtitles available for this YouTube video."
            elif meta.platform == "bilibili" and not bilibili_cred:
                no_sub_reason = "Bilibili account not connected, cannot fetch subtitles."
            elif meta.platform == "xiaoyuzhou":
                no_sub_reason = "Podcast platform (no subtitles)."
            else:
                no_sub_reason = "No subtitles found."

            transcript_method = "whisper"
            logger.info("[analysis] Task #%d — no subtitles, using audio transcription", task.id)
            if meta.platform != "xiaoyuzhou":
                yield ProgressEvent(
                    stage="subtitle_check", progress=14,
                    message=f"{no_sub_reason} Will download audio and use Whisper API.",
                    detail={"method": "whisper", "reason": no_sub_reason},
                )

            # Stage 3: Download audio
            yield ProgressEvent(
                stage="audio_download", progress=15,
                message=f"Downloading audio from {meta.platform}...",
            )
            await update_task_status(db, task, "downloading")

            task_dir = get_task_temp_dir(task)
            if meta.platform == "xiaoyuzhou":
                audio_path = task_dir / "audio.m4a"
            else:
                audio_path = task_dir / "audio.mp3"

            if audio_path.exists() and audio_path.stat().st_size >= MIN_AUDIO_BYTES:
                # Size alone doesn't prove the download finished — an
                # interrupted stream leaves a large but truncated file that
                # fails the split/transcribe step every time. Probe it and
                # compare duration with metadata before trusting the cache.
                cached_ok = True
                try:
                    cached_duration = get_audio_duration(audio_path)
                    if meta.duration_seconds > 0 and cached_duration < meta.duration_seconds - 5.0:
                        cached_ok = False
                        logger.warning(
                            "[analysis] Task #%d — cached audio truncated (%.0fs of expected %ds), re-downloading",
                            task.id, cached_duration, meta.duration_seconds,
                        )
                except Exception as e:
                    cached_ok = False
                    logger.warning(
                        "[analysis] Task #%d — cached audio unparseable (%s), re-downloading",
                        task.id, e,
                    )
                if not cached_ok:
                    audio_path.unlink(missing_ok=True)
            if audio_path.exists() and audio_path.stat().st_size >= MIN_AUDIO_BYTES:
                file_size_mb = audio_path.stat().st_size / (1024 * 1024)
                logger.info("[analysis] Task #%d — using cached audio (%.1f MB)", task.id, file_size_mb)
                yield ProgressEvent(
                    stage="audio_download", progress=55,
                    message=f"Using cached audio file ({file_size_mb:.1f} MB).",
                    detail={"cached": True, "size_mb": round(file_size_mb, 1)},
                )
            else:
                if audio_path.exists():
                    logger.warning("[analysis] Task #%d — cached audio too small (%d bytes), re-downloading",
                                   task.id, audio_path.stat().st_size)
                    audio_path.unlink(missing_ok=True)
                try:
                    if meta.platform == "xiaoyuzhou":
                        if not audio_url:
                            raise RuntimeError("Missing 小宇宙 episode audio URL")

                        def _xy_factory(*, progress_callback=None):
                            return download_xiaoyuzhou_audio(
                                audio_url, task_dir, progress_callback=progress_callback,
                            )

                        async for ev in _relay_download_progress(
                            "audio_download", 15, 40, _xy_factory,
                            label=f"下载音频 · {meta.platform}",
                            cancel_event=cancel_event,
                        ):
                            yield ev
                    else:
                        def _ydbili_factory(*, progress_callback=None):
                            # download_audio's internal yt_dlp call is synchronous
                            # and blocking; run it in a thread to keep the event
                            # loop free to consume progress callbacks.
                            async def _runner() -> Path:
                                return await asyncio.to_thread(
                                    _sync_download_audio,
                                    task.url, task_dir, progress_callback,
                                    cred_kw,
                                )
                            return _runner()

                        async for ev in _relay_download_progress(
                            "audio_download", 15, 40, _ydbili_factory,
                            label=f"下载音频 · {meta.platform}",
                            cancel_event=cancel_event,
                        ):
                            yield ev
                except Exception as e:
                    logger.error("[analysis] Task #%d — audio download failed: %s", task.id, describe_error(e))
                    await update_task_status(db, task, "failed_download", describe_error(e))
                    raise

                file_size_mb = audio_path.stat().st_size / (1024 * 1024)
                _check_cancelled()

            _check_cancelled()

            # Stage 4: Transcribe (with sub-step progress)
            yield ProgressEvent(
                stage="transcription", progress=56,
                message="Starting transcription...",
                detail={"audio_size_mb": round(file_size_mb, 1)},
            )
            await update_task_status(db, task, "transcribing")

            progress_queue: asyncio.Queue[TranscriptionProgress] = asyncio.Queue()

            def _on_transcription_progress(p: TranscriptionProgress) -> None:
                progress_queue.put_nowait(p)

            transcription_task = asyncio.create_task(
                transcribe_audio(audio_path, progress_callback=_on_transcription_progress)
            )

            try:
                while not transcription_task.done():
                    try:
                        tp = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                        pct = _transcription_progress_pct(tp)
                        yield ProgressEvent(
                            stage="transcription", progress=pct,
                            message=tp.message,
                            detail={
                                "sub_step": tp.step,
                                "chunk_index": tp.chunk_index,
                                "total_chunks": tp.total_chunks,
                                **(tp.extra or {}),
                            },
                        )
                    except asyncio.TimeoutError:
                        _check_cancelled()
                        continue

                subtitles, asr_usage = transcription_task.result()
            except Exception as e:
                if not transcription_task.done():
                    transcription_task.cancel()
                logger.error("[analysis] Task #%d — transcription failed: %s", task.id, describe_error(e))
                await update_task_status(db, task, "failed_transcribe", describe_error(e))
                raise

            # drain remaining progress events
            while not progress_queue.empty():
                tp = progress_queue.get_nowait()
                pct = _transcription_progress_pct(tp)
                yield ProgressEvent(
                    stage="transcription", progress=pct,
                    message=tp.message,
                    detail={"sub_step": tp.step, "chunk_index": tp.chunk_index, "total_chunks": tp.total_chunks},
                )

            _check_cancelled()
            logger.info("[analysis] Task #%d — transcription done: %d segments", task.id, len(subtitles))
            yield ProgressEvent(
                stage="transcription", progress=85,
                message=f"Transcription complete ({len(subtitles)} segments).",
                detail={"entry_count": len(subtitles)},
            )

        meta.duration_seconds = _reconcile_duration(meta, subtitles, task.id)

        # Stage 4.5: Translate subtitles to Chinese if not already
        from video_split.service.analyzer import _is_mostly_chinese, translate_subtitles_to_chinese
        if not _is_mostly_chinese(subtitles):
            yield ProgressEvent(
                stage="translation", progress=86,
                message=f"Translating {len(subtitles)} subtitles to Chinese...",
            )
            logger.info("[analysis] Task #%d — translating %d subtitles to Chinese", task.id, len(subtitles))
            subtitles = await translate_subtitles_to_chinese(subtitles)

        # Stage 5: LLM analysis
        settings = get_settings()
        llm_model = settings.llm.model
        yield ProgressEvent(
            stage="analysis", progress=87,
            message=f"Analyzing content with LLM ({llm_model})...",
            detail={
                "method": transcript_method,
                "subtitle_count": len(subtitles),
                "llm_model": llm_model,
            },
        )
        await update_task_status(db, task, "analyzing")

        try:
            analysis = await analyze_transcript(subtitles, meta.duration_seconds)
        except Exception as e:
            logger.error("[analysis] Task #%d — LLM analysis failed: %s", task.id, describe_error(e))
            transcript_text = json.dumps(
                [{"start": s.start, "duration": s.duration, "text": s.text} for s in subtitles]
            )
            task.progress_data = transcript_text
            await update_task_status(db, task, "failed_analyze", describe_error(e))
            raise

        _check_cancelled()
        logger.info("[analysis] Task #%d — LLM analysis done: %d segments", task.id, len(analysis.segments))
        yield ProgressEvent(
            stage="analysis", progress=95,
            message=f"Analysis complete — {len(analysis.segments)} segments identified.",
            detail={"segment_count": len(analysis.segments)},
        )

        # Stage 6: Save results
        video, usage_data = await _save_analysis_results(db, task, meta, analysis, subtitles, asr_usage)
        logger.info("[analysis] Task #%d — complete, video_id=%d", task.id, video.id)
        yield ProgressEvent(
            stage="complete", progress=100, message="Analysis complete!",
            detail={"video_id": video.id, "usage": usage_data},
        )

    except AnalysisCancelled:
        logger.info("[analysis] Task #%d — cancelled by user", task.id)
        await update_task_status(db, task, "cancelled")
        await cleanup_task_files(task)
        yield ProgressEvent(stage="cancelled", progress=0, message="Analysis cancelled.")

    except Exception as e:
        logger.error("[analysis] Task #%d — unhandled error: %s", task.id, e)
        try:
            if task.status == "downloading":
                await update_task_status(db, task, "failed_download", describe_error(e))
            elif task.status == "analyzing":
                if subtitles:
                    task.progress_data = json.dumps(
                        [{"start": s.start, "duration": s.duration, "text": s.text} for s in subtitles]
                    )
                await update_task_status(db, task, "failed_analyze", describe_error(e))
            elif task.status == "transcribing":
                await update_task_status(db, task, "failed_transcribe", describe_error(e))
        except Exception:
            logger.exception("[analysis] Task #%d — failed to update status after error", task.id)
        yield ProgressEvent(stage="error", progress=0, message=describe_error(e))
        raise


async def _download_audio_wrapper(
    url: str,
    output_dir: Path,
    progress_callback=None,
    *,
    sessdata: str = "",
    bili_jct: str = "",
    buvid3: str = "",
) -> Path:
    """Wrapper to call async download_audio from sync executor context.

    Forwards ``progress_callback`` to ``download_audio`` so the relay helper
    can observe download progress (yt_dlp progress_hook runs in a worker
    thread; the relay bridges it back via ``call_soon_threadsafe``).
    """
    return await download_audio(
        url, output_dir, progress_callback,
        sessdata=sessdata, bili_jct=bili_jct, buvid3=buvid3,
    )


def _sync_download_audio(
    url: str,
    output_dir: Path,
    progress_callback,
    cred_kw: dict[str, str],
) -> Path:
    """Run the (async-bodied but synchronous) ``download_audio`` in a worker
    thread via ``asyncio.run``.

    ``download_audio`` is declared ``async`` but its body is fully synchronous
    (yt_dlp blocking). Running it inside ``asyncio.run`` in a worker thread
    keeps the main event loop responsive while yt_dlp's ``progress_hook``
    invokes ``progress_callback`` from that worker thread; the relay bridges
    the callback back to the main loop via ``call_soon_threadsafe``.
    """
    return asyncio.run(download_audio(
        url, output_dir, progress_callback,
        sessdata=cred_kw.get("sessdata", ""),
        bili_jct=cred_kw.get("bili_jct", ""),
        buvid3=cred_kw.get("buvid3", ""),
    ))


async def _relay_download_progress(
    stage: str,
    base_pct: int,
    span_pct: int,
    factory,
    *,
    label: str = "下载音频",
    cancel_event: asyncio.Event | None = None,
) -> AsyncGenerator[ProgressEvent, None]:
    """Bridge a download function's ``progress_callback`` to ProgressEvent yield.

    ``factory`` receives a ``progress_callback`` and returns an awaitable that
    performs the actual download. The callback receives ``ratio: float`` in
    ``[0, 1]``. This helper:

    - Spawns the download as an ``asyncio.Task``.
    - Pumps callback updates through an ``asyncio.Queue`` (thread-safe via
      ``call_soon_threadsafe`` so yt_dlp worker threads are safe; same-loop
      callers like httpx are also fine).
    - Throttles yields: emit only when progress advanced by ≥1% or ≥0.5s
      elapsed since the last yield.
    - Cancels and exits when ``cancel_event`` is set.
    - Propagates any exception from the download task.
    - Emits a closing event at ``base_pct + span_pct`` on successful
      completion (reusing the last seen byte counters so the front-end does
      not flash back to 0). On cancel / exception, no closing event is emitted.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict] = asyncio.Queue()

    def _on_progress(payload: dict) -> None:
        # yt_dlp progress_hook runs in a worker thread; httpx runs in-loop.
        # call_soon_threadsafe is safe in both cases.
        loop.call_soon_threadsafe(queue.put_nowait, payload)

    download_task = asyncio.create_task(factory(progress_callback=_on_progress))

    last_yield_pct: float | None = None
    last_yield_monotonic: float = loop.time()
    last_payload: dict = {"ratio": 0.0, "downloaded_bytes": 0, "total_bytes": 0}

    def _format_message(payload: dict) -> str:
        ratio = float(payload.get("ratio", 0.0))
        pct = int(ratio * 100)
        downloaded = int(payload.get("downloaded_bytes", 0))
        total = int(payload.get("total_bytes", 0))
        dl_mb = downloaded / (1024 * 1024)
        if total > 0:
            total_mb = total / (1024 * 1024)
            return f"{label} {pct}% · {dl_mb:.1f} / {total_mb:.1f} MB"
        if downloaded > 0:
            return f"{label} {pct}% · {dl_mb:.1f} MB"
        return f"{label} {pct}%"

    # opening event so the front-end immediately sees movement to base_pct
    yield ProgressEvent(
        stage=stage, progress=base_pct,
        message=f"{label} 0%",
        detail={"ratio": 0.0, "downloaded_bytes": 0, "total_bytes": 0},
    )
    last_yield_pct = base_pct

    try:
        while not download_task.done():
            if cancel_event is not None and cancel_event.is_set():
                download_task.cancel()
                return
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=0.3)
            except asyncio.TimeoutError:
                # loop back: re-check cancel + task.done()
                continue

            ratio = float(payload.get("ratio", 0.0))
            progress = base_pct + ratio * span_pct
            now = loop.time()
            pct_delta = progress - (last_yield_pct if last_yield_pct is not None else 0)
            time_delta = now - last_yield_monotonic
            # throttle: only yield on meaningful change or after half a second
            if pct_delta >= 1.0 or time_delta >= 0.5 or ratio >= 1.0:
                last_payload = payload
                yield ProgressEvent(
                    stage=stage,
                    progress=progress,
                    message=_format_message(payload),
                    detail=payload,
                )
                last_yield_pct = progress
                last_yield_monotonic = now

        # propagate any exception raised inside the download task
        await download_task
    except asyncio.CancelledError:
        if not download_task.done():
            download_task.cancel()
        raise
    finally:
        if not download_task.done():
            download_task.cancel()
            try:
                await download_task
            except (asyncio.CancelledError, Exception):
                # swallow: the task was cancelled by us; its error is not the
                # caller's concern. The original outcome (cancel/exception) is
                # already being propagated via return/raise above.
                pass

    # closing event to lock the terminal percentage at base+span — reuse last
    # seen byte counters so the front-end does not flash 0.0 MB at the end.
    yield ProgressEvent(
        stage=stage,
        progress=base_pct + span_pct,
        message=f"{label} 100%",
        detail={**last_payload, "ratio": 1.0},
    )


async def resume_analysis(
    db: AsyncSession,
    task: Task,
    cancel_event: asyncio.Event,
    bilibili_cred: BilibiliCredential | None = None,
) -> AsyncGenerator[ProgressEvent, None]:
    """Resume a failed task from the point of failure."""
    cred_kw = _cred_kwargs(bilibili_cred)
    task_dir = get_task_temp_dir(task)
    logger.info("[analysis] Resuming task #%d (status=%s)", task.id, task.status)

    platform_hint, _ = detect_platform(task.url)
    xy_audio_url = ""
    if platform_hint == "xiaoyuzhou":
        meta, xy_audio_url = await extract_xiaoyuzhou_metadata(task.url)
    else:
        meta = await extract_metadata(task.url, **cred_kw)
    local_thumb = await download_thumbnail(meta)
    if local_thumb:
        meta.thumbnail_url = local_thumb
    duration_str = f"{meta.duration_seconds // 60}m{meta.duration_seconds % 60:02d}s"
    yield ProgressEvent(
        stage="metadata", progress=5, message=f"Video: {meta.title}",
        detail={
            "title": meta.title,
            "platform": meta.platform,
            "duration": duration_str,
            "duration_seconds": meta.duration_seconds,
            "thumbnail_url": meta.thumbnail_url,
            "uploader": meta.uploader,
            "upload_date": meta.upload_date,
        },
    )

    if task.status == "failed_download":
        logger.info("[analysis] Task #%d — retrying from download stage", task.id)
        yield ProgressEvent(stage="audio_download", progress=15, message="Retrying audio download...")
        await update_task_status(db, task, "downloading")

        task_dir_dl = get_task_temp_dir(task)
        if platform_hint == "xiaoyuzhou":
            audio_path_dl = task_dir_dl / "audio.m4a"
        else:
            audio_path_dl = task_dir_dl / "audio.mp3"

        try:
            if platform_hint == "xiaoyuzhou":
                if not xy_audio_url:
                    raise RuntimeError("Missing 小宇宙 episode audio URL")

                def _xy_factory(*, progress_callback=None):
                    return download_xiaoyuzhou_audio(
                        xy_audio_url, task_dir_dl, progress_callback=progress_callback,
                    )

                async for ev in _relay_download_progress(
                    "audio_download", 15, 40, _xy_factory,
                    label=f"下载音频 · {meta.platform}",
                    cancel_event=cancel_event,
                ):
                    yield ev
                # pick up the path written by the download
                audio_path_dl = task_dir_dl / "audio.m4a"
            else:
                def _ydbili_factory(*, progress_callback=None):
                    async def _runner() -> Path:
                        return await asyncio.to_thread(
                            _sync_download_audio,
                            task.url, task_dir_dl, progress_callback,
                            cred_kw,
                        )
                    return _runner()

                async for ev in _relay_download_progress(
                    "audio_download", 15, 40, _ydbili_factory,
                    label=f"下载音频 · {meta.platform}",
                    cancel_event=cancel_event,
                ):
                    yield ev
                audio_files = list(task_dir_dl.glob("audio.*"))
                if not audio_files:
                    raise RuntimeError("Audio download retry failed: no output file")
                audio_path_dl = audio_files[0]
        except XiaoyuzhouError as e:
            logger.error("[analysis] Task #%d — xiaoyuzhou retry failed (%s): %s", task.id, e.code, e)
            await update_task_status(db, task, "failed_download", describe_error(e))
            yield ProgressEvent(
                stage="error", progress=0, message=describe_error(e),
                detail={"error_code": e.code},
            )
            return
        except Exception as e:
            logger.error("[analysis] Task #%d — download retry failed: %s", task.id, describe_error(e))
            await update_task_status(db, task, "failed_download", describe_error(e))
            yield ProgressEvent(stage="error", progress=0, message=describe_error(e))
            return

        file_size_mb = audio_path_dl.stat().st_size / (1024 * 1024)
        yield ProgressEvent(stage="audio_download", progress=55,
                            message=f"Audio downloaded ({file_size_mb:.1f} MB).")

        yield ProgressEvent(stage="transcription", progress=56, message="Starting transcription...")
        await update_task_status(db, task, "transcribing")

        progress_queue_dl: asyncio.Queue[TranscriptionProgress] = asyncio.Queue()
        transcription_task_dl = asyncio.create_task(
            transcribe_audio(audio_path_dl, progress_callback=lambda p: progress_queue_dl.put_nowait(p))
        )
        try:
            while not transcription_task_dl.done():
                try:
                    tp = await asyncio.wait_for(progress_queue_dl.get(), timeout=1.0)
                    yield ProgressEvent(
                        stage="transcription", progress=_transcription_progress_pct(tp),
                        message=tp.message,
                        detail={"sub_step": tp.step, "chunk_index": tp.chunk_index, "total_chunks": tp.total_chunks},
                    )
                except asyncio.TimeoutError:
                    continue
            subtitles_dl, asr_usage_dl = transcription_task_dl.result()
        except Exception as e:
            if not transcription_task_dl.done():
                transcription_task_dl.cancel()
            logger.error("[analysis] Task #%d — transcription failed on download-retry: %s", task.id, describe_error(e))
            await update_task_status(db, task, "failed_transcribe", describe_error(e))
            yield ProgressEvent(stage="error", progress=0, message=describe_error(e))
            return

        yield ProgressEvent(stage="transcription", progress=85, message="Transcription complete.")
        yield ProgressEvent(stage="analysis", progress=87, message="Analyzing with LLM...")
        await update_task_status(db, task, "analyzing")

        meta.duration_seconds = _reconcile_duration(meta, subtitles_dl, task.id)

        try:
            analysis_dl = await analyze_transcript(subtitles_dl, meta.duration_seconds)
        except Exception as e:
            logger.error("[analysis] Task #%d — LLM failed on download-retry: %s", task.id, describe_error(e))
            task.progress_data = json.dumps(
                [{"start": s.start, "duration": s.duration, "text": s.text} for s in subtitles_dl]
            )
            await update_task_status(db, task, "failed_analyze", describe_error(e))
            yield ProgressEvent(stage="error", progress=0, message=describe_error(e))
            return

        video_dl, usage_data_dl = await _save_analysis_results(
            db, task, meta, analysis_dl, subtitles_dl, asr_usage_dl,
        )
        logger.info("[analysis] Task #%d — download-retry complete, video_id=%d", task.id, video_dl.id)
        yield ProgressEvent(stage="complete", progress=100, message="Analysis complete!",
                            detail={"video_id": video_dl.id, "usage": usage_data_dl})

    elif task.status in ("failed_transcribe", "downloaded"):
        audio_files = list(task_dir.glob("audio.*"))
        if not audio_files or audio_files[0].stat().st_size < MIN_AUDIO_BYTES:
            reason = "Cached audio file not found" if not audio_files else (
                f"Cached audio file too small ({audio_files[0].stat().st_size} bytes)"
            )
            await update_task_status(db, task, "failed_download", reason)
            yield ProgressEvent(stage="error", progress=0, message=f"{reason}. Please retry to re-download.")
            return

        # Integrity check: a partial/interrupted download can still be large
        # enough to pass the size gate above, then fail the split/transcribe
        # step on every resume — the task would hit the same wall forever.
        # Probe the file and compare its duration with the metadata; if it is
        # unparseable or clearly truncated, downgrade to re-download instead.
        cached_audio = audio_files[0]
        try:
            cached_duration = get_audio_duration(cached_audio)
        except Exception as e:
            reason = f"Cached audio file is corrupt (ffprobe failed: {e})"
            logger.warning("[analysis] Task #%d — %s; forcing re-download", task.id, reason)
            cached_audio.unlink(missing_ok=True)
            await update_task_status(db, task, "failed_download", reason)
            yield ProgressEvent(stage="error", progress=0, message=f"{reason}. Please retry to re-download.")
            return
        if meta.duration_seconds > 0 and cached_duration < meta.duration_seconds - 5.0:
            reason = (
                f"Cached audio is truncated ({cached_duration:.0f}s of "
                f"expected {meta.duration_seconds}s)"
            )
            logger.warning("[analysis] Task #%d — %s; forcing re-download", task.id, reason)
            cached_audio.unlink(missing_ok=True)
            await update_task_status(db, task, "failed_download", reason)
            yield ProgressEvent(stage="error", progress=0, message=f"{reason}. Please retry to re-download.")
            return

        yield ProgressEvent(stage="transcription", progress=56, message="Resuming transcription...")
        await update_task_status(db, task, "transcribing")

        progress_queue: asyncio.Queue[TranscriptionProgress] = asyncio.Queue()
        transcription_task = asyncio.create_task(
            transcribe_audio(audio_files[0], progress_callback=lambda p: progress_queue.put_nowait(p))
        )
        try:
            while not transcription_task.done():
                try:
                    tp = await asyncio.wait_for(progress_queue.get(), timeout=1.0)
                    yield ProgressEvent(
                        stage="transcription", progress=_transcription_progress_pct(tp),
                        message=tp.message,
                        detail={"sub_step": tp.step, "chunk_index": tp.chunk_index, "total_chunks": tp.total_chunks},
                    )
                except asyncio.TimeoutError:
                    continue
            subtitles, asr_usage = transcription_task.result()
        except Exception as e:
            if not transcription_task.done():
                transcription_task.cancel()
            logger.error("[analysis] Task #%d — transcription failed on resume: %s", task.id, e)
            await update_task_status(db, task, "failed_transcribe", describe_error(e))
            yield ProgressEvent(stage="error", progress=0, message=describe_error(e))
            return

        logger.info("[analysis] Task #%d — transcription done: %d segments", task.id, len(subtitles))
        yield ProgressEvent(stage="transcription", progress=85, message="Transcription complete.")

        yield ProgressEvent(stage="analysis", progress=87, message="Analyzing with LLM...")
        await update_task_status(db, task, "analyzing")
        meta.duration_seconds = _reconcile_duration(meta, subtitles, task.id)
        try:
            analysis = await analyze_transcript(subtitles, meta.duration_seconds)
        except Exception as e:
            logger.error("[analysis] Task #%d — LLM analysis failed on resume: %s", task.id, e)
            task.progress_data = json.dumps(
                [{"start": s.start, "duration": s.duration, "text": s.text} for s in subtitles]
            )
            await update_task_status(db, task, "failed_analyze", describe_error(e))
            yield ProgressEvent(stage="error", progress=0, message=describe_error(e))
            return

        video, usage_data = await _save_analysis_results(db, task, meta, analysis, subtitles, asr_usage)
        logger.info("[analysis] Task #%d — resume complete, video_id=%d", task.id, video.id)
        yield ProgressEvent(stage="complete", progress=100, message="Analysis complete!",
                            detail={"video_id": video.id, "usage": usage_data})

    elif task.status == "failed_analyze":
        yield ProgressEvent(stage="analysis", progress=87, message="Resuming LLM analysis...")
        await update_task_status(db, task, "analyzing")

        asr_usage = ASRUsage()
        saved_transcript = task.progress_data
        try:
            parsed = json.loads(saved_transcript) if saved_transcript else None
        except (json.JSONDecodeError, TypeError):
            logger.warning("[analysis] Task #%d — corrupted progress_data, will re-transcribe", task.id)
            parsed = None
        if isinstance(parsed, list) and len(parsed) > 0:
            from video_split.service.downloader import SubtitleEntry as SE
            subtitles = [SE(start=s["start"], duration=s["duration"], text=s["text"]) for s in parsed]
        else:
            audio_files = list(task_dir.glob("audio.*"))
            if not audio_files:
                yield ProgressEvent(stage="error", progress=0, message="No cached data found.")
                return
            yield ProgressEvent(stage="transcription", progress=56, message="Re-transcribing audio...")
            progress_queue_retry: asyncio.Queue[TranscriptionProgress] = asyncio.Queue()
            transcription_task_retry = asyncio.create_task(
                transcribe_audio(
                    audio_files[0],
                    progress_callback=lambda p: progress_queue_retry.put_nowait(p),
                )
            )
            try:
                while not transcription_task_retry.done():
                    try:
                        tp = await asyncio.wait_for(progress_queue_retry.get(), timeout=1.0)
                        yield ProgressEvent(
                            stage="transcription",
                            progress=_transcription_progress_pct(tp),
                            message=tp.message,
                            detail={
                                "sub_step": tp.step,
                                "chunk_index": tp.chunk_index,
                                "total_chunks": tp.total_chunks,
                            },
                        )
                    except asyncio.TimeoutError:
                        continue
                subtitles, asr_usage = transcription_task_retry.result()
            except Exception as e:
                if not transcription_task_retry.done():
                    transcription_task_retry.cancel()
                logger.error(
                    "[analysis] Task #%d — re-transcription failed: %s",
                    task.id, describe_error(e),
                )
                await update_task_status(db, task, "failed_transcribe", describe_error(e))
                yield ProgressEvent(stage="error", progress=0, message=describe_error(e))
                return
            yield ProgressEvent(stage="transcription", progress=85, message="Transcription complete.")

        meta.duration_seconds = _reconcile_duration(meta, subtitles, task.id)

        try:
            analysis = await analyze_transcript(subtitles, meta.duration_seconds)
        except Exception as e:
            logger.error("[analysis] Task #%d — LLM analysis failed on resume: %s", task.id, e)
            await update_task_status(db, task, "failed_analyze", describe_error(e))
            yield ProgressEvent(stage="error", progress=0, message=describe_error(e))
            return

        video, usage_data = await _save_analysis_results(db, task, meta, analysis, subtitles, asr_usage)
        logger.info("[analysis] Task #%d — resume complete, video_id=%d", task.id, video.id)
        yield ProgressEvent(stage="complete", progress=100, message="Analysis complete!",
                            detail={"video_id": video.id, "usage": usage_data})
