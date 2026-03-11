from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from video_split.config import get_settings
from sqlalchemy import select

from video_split.models import BilibiliCredential, Segment, Tag, Task, Video, video_tags
from video_split.schemas import ProgressEvent
from video_split.service.analyzer import analyze_transcript
from video_split.service.downloader import (
    SubtitleEntry,
    download_audio,
    download_thumbnail,
    extract_metadata,
    fetch_bilibili_subtitles,
    fetch_youtube_subtitles,
)
from video_split.service.task_manager import (
    cleanup_task_files,
    get_task_temp_dir,
    update_task_status,
)
from video_split.service.transcriber import ASRUsage, TranscriptionProgress, transcribe_audio

logger = logging.getLogger(__name__)

_PLATFORM_TAG_MAP = {
    "youtube": ("YouTube", "#ff0000"),
    "bilibili": ("Bilibili", "#00a1d6"),
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


async def run_analysis(
    db: AsyncSession,
    task: Task,
    cancel_event: asyncio.Event,
    bilibili_cred: BilibiliCredential | None = None,
    confirm_event: asyncio.Event | None = None,
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
        meta = await extract_metadata(task.url, **cred_kw)
        _check_cancelled()

        local_thumb = await download_thumbnail(meta)
        if local_thumb:
            meta.thumbnail_url = local_thumb

        await update_task_status(db, task, "downloading", video_title=meta.title)

        duration = meta.duration_seconds
        max_dur = settings.video.max_duration_seconds
        if duration > max_dur:
            raise ValueError(
                f"Video is {duration // 3600}h{(duration % 3600) // 60}m, "
                f"exceeding the {max_dur // 3600}h{(max_dur % 3600) // 60}m limit."
            )

        threshold = settings.video.confirm_threshold_seconds
        if confirm_event and duration > threshold:
            duration_str_confirm = f"{duration // 60}m{duration % 60:02d}s"
            yield ProgressEvent(
                stage="confirm_required", progress=10,
                message=f"Video duration {duration_str_confirm} exceeds {threshold // 60} min threshold. Please confirm to continue.",
                detail={"task_id": task.id, "title": meta.title, "duration_seconds": duration,
                        "thumbnail_url": meta.thumbnail_url},
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
                "thumbnail_url": meta.thumbnail_url,
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
            subtitles = await fetch_bilibili_subtitles(
                task.url, **cred_kw,
            )
        elif meta.platform == "bilibili":
            yield ProgressEvent(
                stage="subtitle_check", progress=12,
                message="Bilibili account not connected, will use audio transcription.",
                detail={"hint": "bilibili_not_connected"},
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
            else:
                no_sub_reason = "No subtitles found."

            transcript_method = "whisper"
            logger.info("[analysis] Task #%d — no subtitles, using audio transcription", task.id)
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
            audio_path = task_dir / "audio.mp3"

            if audio_path.exists():
                file_size_mb = audio_path.stat().st_size / (1024 * 1024)
                logger.info("[analysis] Task #%d — using cached audio (%.1f MB)", task.id, file_size_mb)
                yield ProgressEvent(
                    stage="audio_download", progress=55,
                    message=f"Using cached audio file ({file_size_mb:.1f} MB).",
                    detail={"cached": True, "size_mb": round(file_size_mb, 1)},
                )
            else:
                try:
                    audio_path = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: asyncio.run(
                            _download_audio_wrapper(task.url, task_dir, **cred_kw)
                        )
                    )
                except Exception as e:
                    logger.error("[analysis] Task #%d — audio download failed: %s", task.id, e)
                    await update_task_status(db, task, "failed_download", str(e))
                    await cleanup_task_files(task)
                    raise

                file_size_mb = audio_path.stat().st_size / (1024 * 1024)
                yield ProgressEvent(
                    stage="audio_download", progress=55,
                    message=f"Audio downloaded ({file_size_mb:.1f} MB).",
                    detail={"cached": False, "size_mb": round(file_size_mb, 1)},
                )

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
                logger.error("[analysis] Task #%d — transcription failed: %s", task.id, e)
                await update_task_status(db, task, "failed_transcribe", str(e))
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
            logger.error("[analysis] Task #%d — LLM analysis failed: %s", task.id, e)
            transcript_text = json.dumps(
                [{"start": s.start, "duration": s.duration, "text": s.text} for s in subtitles]
            )
            task.progress_data = transcript_text
            await update_task_status(db, task, "failed_analyze", str(e))
            raise

        _check_cancelled()
        logger.info("[analysis] Task #%d — LLM analysis done: %d segments", task.id, len(analysis.segments))
        yield ProgressEvent(
            stage="analysis", progress=95,
            message=f"Analysis complete — {len(analysis.segments)} segments identified.",
            detail={"segment_count": len(analysis.segments)},
        )

        # Stage 6: Save results
        transcript_text = "\n".join(
            f"[{int(s.start) // 60:02d}:{int(s.start) % 60:02d}] {s.text}" for s in subtitles
        )
        subtitle_data = json.dumps(
            [{"start": s.start, "duration": s.duration, "text": s.text} for s in subtitles],
            ensure_ascii=False,
        )

        llm_u = analysis.llm_usage
        usage_data = {
            "asr_duration_seconds": asr_usage.duration_seconds,
            "asr_model": asr_usage.model,
            "llm_prompt_tokens": llm_u.prompt_tokens,
            "llm_completion_tokens": llm_u.completion_tokens,
            "llm_total_tokens": llm_u.total_tokens,
            "llm_model": llm_u.model,
        }

        video = Video(
            user_id=task.user_id,
            url=meta.url,
            platform=meta.platform,
            video_id=meta.video_id,
            title=meta.title,
            thumbnail_url=meta.thumbnail_url,
            upload_date=meta.upload_date,
            duration_seconds=meta.duration_seconds,
            summary=analysis.summary,
            summary_en=analysis.summary_en,
            raw_transcript=transcript_text,
            subtitle_json=subtitle_data,
            usage_json=json.dumps(usage_data, ensure_ascii=False),
        )
        db.add(video)
        await db.flush()
        await _attach_platform_tag(db, video)

        for seg in analysis.segments:
            db.add(
                Segment(
                    video_id=video.id,
                    segment_index=seg.index,
                    title=seg.title,
                    title_en=seg.title_en,
                    summary=seg.summary,
                    summary_en=seg.summary_en,
                    start_seconds=seg.start_seconds,
                    end_seconds=seg.end_seconds,
                )
            )

        await update_task_status(db, task, "completed")
        await cleanup_task_files(task)
        await db.commit()

        logger.info("[analysis] Task #%d — complete, video_id=%d", task.id, video.id)
        yield ProgressEvent(
            stage="complete",
            progress=100,
            message="Analysis complete!",
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
                await update_task_status(db, task, "failed_download", str(e))
                await cleanup_task_files(task)
            elif task.status == "analyzing":
                if subtitles:
                    task.progress_data = json.dumps(
                        [{"start": s.start, "duration": s.duration, "text": s.text} for s in subtitles]
                    )
                await update_task_status(db, task, "failed_analyze", str(e))
            elif task.status == "transcribing":
                await update_task_status(db, task, "failed_transcribe", str(e))
        except Exception:
            logger.exception("[analysis] Task #%d — failed to update status after error", task.id)
        yield ProgressEvent(stage="error", progress=0, message=str(e))
        raise


async def _download_audio_wrapper(
    url: str,
    output_dir: Path,
    *,
    sessdata: str = "",
    bili_jct: str = "",
    buvid3: str = "",
) -> Path:
    """Wrapper to call async download_audio from sync executor context."""
    return await download_audio(
        url, output_dir,
        sessdata=sessdata, bili_jct=bili_jct, buvid3=buvid3,
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

    if task.status in ("failed_transcribe", "downloaded"):
        audio_files = list(task_dir.glob("audio.*"))
        if not audio_files:
            await update_task_status(db, task, "failed_download", "Cached audio file not found")
            yield ProgressEvent(stage="error", progress=0, message="Cached audio file not found. Please start over.")
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
            await update_task_status(db, task, "failed_transcribe", str(e))
            yield ProgressEvent(stage="error", progress=0, message=str(e))
            return

        logger.info("[analysis] Task #%d — transcription done: %d segments", task.id, len(subtitles))
        yield ProgressEvent(stage="transcription", progress=85, message="Transcription complete.")
        meta = await extract_metadata(task.url, **cred_kw)
        local_thumb = await download_thumbnail(meta)
        if local_thumb:
            meta.thumbnail_url = local_thumb

        yield ProgressEvent(stage="analysis", progress=87, message="Analyzing with LLM...")
        await update_task_status(db, task, "analyzing")
        try:
            analysis = await analyze_transcript(subtitles, meta.duration_seconds)
        except Exception as e:
            logger.error("[analysis] Task #%d — LLM analysis failed on resume: %s", task.id, e)
            await update_task_status(db, task, "failed_analyze", str(e))
            yield ProgressEvent(stage="error", progress=0, message=str(e))
            return

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
        logger.info("[analysis] Task #%d — resume complete, video_id=%d", task.id, video.id)
        yield ProgressEvent(stage="complete", progress=100, message="Analysis complete!", detail={"video_id": video.id, "usage": usage_data})

    elif task.status == "failed_analyze":
        yield ProgressEvent(stage="analysis", progress=87, message="Resuming LLM analysis...")
        await update_task_status(db, task, "analyzing")

        asr_usage = ASRUsage()
        saved_transcript = task.progress_data
        parsed = json.loads(saved_transcript) if saved_transcript else None
        if isinstance(parsed, list) and len(parsed) > 0:
            from video_split.service.downloader import SubtitleEntry as SE
            subtitles = [SE(start=s["start"], duration=s["duration"], text=s["text"]) for s in parsed]
        else:
            audio_files = list(task_dir.glob("audio.*"))
            if not audio_files:
                yield ProgressEvent(stage="error", progress=0, message="No cached data found.")
                return
            yield ProgressEvent(stage="transcription", progress=56, message="Re-transcribing audio...")
            subtitles, asr_usage = await transcribe_audio(audio_files[0])
            yield ProgressEvent(stage="transcription", progress=85, message="Transcription complete.")

        meta = await extract_metadata(task.url, **cred_kw)
        local_thumb = await download_thumbnail(meta)
        if local_thumb:
            meta.thumbnail_url = local_thumb

        try:
            analysis = await analyze_transcript(subtitles, meta.duration_seconds)
        except Exception as e:
            logger.error("[analysis] Task #%d — LLM analysis failed on resume: %s", task.id, e)
            await update_task_status(db, task, "failed_analyze", str(e))
            yield ProgressEvent(stage="error", progress=0, message=str(e))
            return

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
        logger.info("[analysis] Task #%d — resume complete, video_id=%d", task.id, video.id)
        yield ProgressEvent(stage="complete", progress=100, message="Analysis complete!", detail={"video_id": video.id, "usage": usage_data})
