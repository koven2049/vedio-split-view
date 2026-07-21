from __future__ import annotations

import asyncio
import json as _json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from sqlalchemy import select

from video_split.config import get_settings
from video_split.database import get_db
from video_split.dependencies import require_admin
from video_split.models import BilibiliCredential, User
from video_split.schemas import (
    ChunkInfo,
    DebugCleanupResponse,
    DebugDownloadRequest,
    DebugDownloadResponse,
    DebugTaskInfo,
    DebugTestASRRequest,
    DebugTranscribeRequest,
    DebugTranscribeResponse,
    TranscriptSegment,
)
from video_split.service.downloader import detect_platform, download_audio, extract_metadata, normalize_url
from video_split.service.task_manager import (
    DuplicateVideoError,
    QuotaExceededError,
    VideoLimitError,
    check_duplicate,
    check_task_quota,
    check_video_limit,
    cleanup_task_files,
    count_pending_tasks,
    create_task,
    get_task_by_id,
    get_task_temp_dir,
    get_user_tasks,
    update_task_status,
)
from video_split.service.transcriber import (
    get_audio_duration,
    split_audio,
    transcribe_single_chunk,
)

router = APIRouter(prefix="/api/debug", tags=["debug"])

_debug_confirmations: dict[int, asyncio.Event] = {}


def _sse_event(stage: str, progress: int, message: str, detail: dict | None = None) -> dict:
    payload = {"stage": stage, "progress": progress, "message": message}
    if detail:
        payload["detail"] = detail
    return {"event": stage, "data": _json.dumps(payload, ensure_ascii=False)}


@router.post("/download")
async def debug_download(
    req: DebugDownloadRequest,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Download audio via SSE with real-time progress from yt-dlp."""
    req.url = normalize_url(req.url)
    platform, vid = detect_platform(req.url)
    if platform == "unknown":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported URL")

    try:
        existing = await check_duplicate(db, user.id, platform, vid)
        if existing is not None:
            raise DuplicateVideoError(
                f"Video already downloaded: 「{existing.video_title or existing.url}」",
                existing_type="task", existing_id=existing.id,
            )
    except DuplicateVideoError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))

    try:
        await check_task_quota(db, user.id)
    except QuotaExceededError as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e))

    try:
        await check_video_limit(db, user.id)
    except VideoLimitError as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(e))

    cred_kw: dict[str, str] = {}
    if platform == "bilibili":
        result = await db.execute(
            select(BilibiliCredential).where(BilibiliCredential.user_id == user.id)
        )
        cred = result.scalar_one_or_none()
        if cred:
            cred_kw = {"sessdata": cred.sessdata, "bili_jct": cred.bili_jct, "buvid3": cred.buvid3}

    confirm_event = asyncio.Event()

    async def _stream():
        try:
            yield _sse_event("metadata", 5, "Fetching video metadata...")
            meta = await extract_metadata(req.url, **cred_kw)
            duration_str = f"{meta.duration_seconds // 60}m{meta.duration_seconds % 60:02d}s"
            yield _sse_event("metadata", 10, f"Video: {meta.title}", {
                "title": meta.title, "duration": duration_str,
                "duration_seconds": meta.duration_seconds,
            })

            task = await create_task(db, user.id, req.url, platform)
            await update_task_status(db, task, "downloading", video_title=meta.title)
            task_dir = get_task_temp_dir(task)

            settings = get_settings()
            threshold = settings.video.confirm_threshold_seconds
            if meta.duration_seconds > threshold:
                _debug_confirmations[task.id] = confirm_event
                yield _sse_event("confirm_required", 10,
                    f"Video duration {duration_str} exceeds {threshold // 60} min threshold. Please confirm to continue.",
                    {"task_id": task.id, "title": meta.title, "duration_seconds": meta.duration_seconds})
                try:
                    await asyncio.wait_for(confirm_event.wait(), timeout=300)
                except asyncio.TimeoutError:
                    yield _sse_event("error", 0, "Confirmation timed out — task cancelled.")
                    await update_task_status(db, task, "cancelled")
                    await cleanup_task_files(task)
                    return
                finally:
                    _debug_confirmations.pop(task.id, None)

            yield _sse_event("downloading", 12, "Starting audio download...")

            loop = asyncio.get_event_loop()
            progress_q: asyncio.Queue[float | None] = asyncio.Queue()
            last_pct_sent = 0.0

            def _on_progress(pct: float) -> None:
                loop.call_soon_threadsafe(progress_q.put_nowait, pct)

            def _do_download() -> Path:
                import asyncio as _aio
                return _aio.run(download_audio(
                    req.url, task_dir, progress_callback=_on_progress, **cred_kw,
                ))

            download_future = loop.run_in_executor(None, _do_download)

            while not download_future.done():
                try:
                    pct = await asyncio.wait_for(progress_q.get(), timeout=0.5)
                    if pct is not None and pct - last_pct_sent >= 0.02:
                        last_pct_sent = pct
                        overall = 12 + int(pct * 68)
                        yield _sse_event("downloading", overall, f"Downloading audio... {int(pct * 100)}%", {
                            "download_percent": round(pct * 100, 1),
                        })
                except asyncio.TimeoutError:
                    pass

            audio_path = download_future.result()
            file_size_mb = audio_path.stat().st_size / (1024 * 1024)
            yield _sse_event("downloading", 80, f"Audio downloaded ({file_size_mb:.1f} MB)", {
                "download_percent": 100, "size_mb": round(file_size_mb, 1),
            })

            await update_task_status(db, task, "downloaded")

            yield _sse_event("splitting", 85, "Splitting audio into chunks...")
            chunks = split_audio(audio_path)
            chunk_infos = _build_chunk_infos(chunks, audio_path)

            settings = get_settings()
            quota_used = await count_pending_tasks(db, user.id)

            yield _sse_event("complete", 100, "Download complete!", {
                "task_id": task.id, "platform": platform,
                "title": meta.title, "duration_seconds": meta.duration_seconds,
                "audio_path": str(audio_path),
                "audio_size_bytes": audio_path.stat().st_size,
                "chunks": [ci.model_dump() for ci in chunk_infos],
                "chunk_duration_config": settings.transcription.chunk_duration_seconds,
                "quota_used": quota_used,
                "quota_max": settings.storage.max_pending_tasks_per_user,
            })

        except Exception as e:
            yield _sse_event("error", 0, str(e))

    return EventSourceResponse(_stream())


@router.get("/tasks", response_model=list[DebugTaskInfo])
async def debug_list_tasks(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all non-terminal tasks with file details."""
    tasks = await get_user_tasks(db, user.id)
    result = []
    for t in tasks:
        info = DebugTaskInfo(
            task_id=t.id,
            url=t.url,
            platform=t.platform,
            status=t.status,
            title=t.video_title,
            created_at=t.created_at,
        )
        if t.temp_dir:
            task_dir = Path(t.temp_dir)
            audio_files = list(task_dir.glob("audio.*"))
            if audio_files:
                ap = audio_files[0]
                info.audio_path = str(ap)
                info.audio_size_mb = round(ap.stat().st_size / (1024 * 1024), 2)
                chunk_dir = task_dir / "chunks"
                if chunk_dir.exists():
                    chunk_files = sorted(chunk_dir.glob("chunk_*.mp3"))
                    info.chunks = _build_chunk_infos(chunk_files, ap)
        result.append(info)
    return result


@router.post("/transcribe", response_model=DebugTranscribeResponse)
async def debug_transcribe(
    req: DebugTranscribeRequest,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Transcribe a downloaded audio. Optionally specify chunk_index to transcribe one chunk only."""
    task = await get_task_by_id(db, req.task_id, user.id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    if task.status not in ("downloaded", "failed_transcribe"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Task status is '{task.status}', expected 'downloaded' or 'failed_transcribe'",
        )

    task_dir = get_task_temp_dir(task)
    audio_files = list(task_dir.glob("audio.*"))
    if not audio_files:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Audio file not found in task directory")
    audio_path = audio_files[0]

    chunk_dir = task_dir / "chunks"
    if chunk_dir.exists():
        chunk_files = sorted(chunk_dir.glob("chunk_*.mp3"))
    else:
        chunk_files = split_audio(audio_path)

    if req.chunk_index is not None:
        if req.chunk_index < 0 or req.chunk_index >= len(chunk_files):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"chunk_index {req.chunk_index} out of range (0-{len(chunk_files) - 1})",
            )
        target = chunk_files[req.chunk_index]
        entries = await transcribe_single_chunk(target)
        return DebugTranscribeResponse(
            task_id=task.id,
            chunk_file=str(target),
            segments=[TranscriptSegment(start=e.start, duration=e.duration, text=e.text) for e in entries],
            total_segments=len(entries),
        )

    await update_task_status(db, task, "transcribing")
    all_segments: list[TranscriptSegment] = []
    time_offset = 0.0

    try:
        for chunk_path in chunk_files:
            entries = await transcribe_single_chunk(chunk_path)
            for e in entries:
                all_segments.append(
                    TranscriptSegment(start=e.start + time_offset, duration=e.duration, text=e.text)
                )
            if entries:
                last = entries[-1]
                time_offset += last.start + last.duration + 0.5
    except Exception as e:
        await update_task_status(db, task, "failed_transcribe", str(e))
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Transcription failed: {e}")

    await update_task_status(db, task, "downloaded")

    return DebugTranscribeResponse(
        task_id=task.id,
        chunk_file=None,
        segments=all_segments,
        total_segments=len(all_segments),
    )


@router.delete("/tasks/{task_id}", response_model=DebugCleanupResponse)
async def debug_cleanup(
    task_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete temp files and remove task record."""
    task = await get_task_by_id(db, task_id, user.id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")

    files_removed = 0
    if task.temp_dir:
        temp_path = Path(task.temp_dir)
        if temp_path.exists():
            files_removed = sum(1 for _ in temp_path.rglob("*") if _.is_file())
            shutil.rmtree(temp_path, ignore_errors=True)

    await db.delete(task)
    await db.commit()

    return DebugCleanupResponse(
        task_id=task_id,
        files_removed=files_removed,
        status="removed",
    )


@router.get("/tasks/{task_id}/chunks", response_model=list[ChunkInfo])
async def debug_list_chunks(
    task_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List chunk files for a downloaded task. If not yet split, split now."""
    task = await get_task_by_id(db, task_id, user.id)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")

    task_dir = get_task_temp_dir(task)
    audio_files = list(task_dir.glob("audio.*"))
    if not audio_files:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Audio file not found")

    audio_path = audio_files[0]
    chunk_dir = task_dir / "chunks"
    if chunk_dir.exists():
        chunk_files = sorted(chunk_dir.glob("chunk_*.mp3"))
    else:
        chunk_files = split_audio(audio_path)

    return _build_chunk_infos(chunk_files, audio_path)


@router.post("/test-asr", response_model=DebugTranscribeResponse)
async def debug_test_asr(
    req: DebugTestASRRequest,
    user: User = Depends(require_admin),
):
    """Transcribe a local audio file directly (no task/quota required).

    Useful for quickly verifying ASR provider connectivity and output format.
    """
    audio_path = Path(req.file_path)
    if not audio_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"File not found: {req.file_path}")

    try:
        entries = await transcribe_single_chunk(audio_path)
    except Exception as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Transcription failed: {e}")

    return DebugTranscribeResponse(
        task_id=0,
        chunk_file=str(audio_path),
        segments=[
            TranscriptSegment(start=e.start, duration=e.duration, text=e.text)
            for e in entries
        ],
        total_segments=len(entries),
    )


def _build_chunk_infos(chunk_files: list[Path], audio_path: Path) -> list[ChunkInfo]:
    infos: list[ChunkInfo] = []
    for i, cp in enumerate(chunk_files):
        if not cp.exists():
            continue
        try:
            dur = get_audio_duration(cp)
        except Exception:
            dur = 0.0
        infos.append(ChunkInfo(
            index=i,
            filename=cp.name,
            path=str(cp),
            size_bytes=cp.stat().st_size,
            duration_seconds=round(dur, 2),
        ))
    return infos
