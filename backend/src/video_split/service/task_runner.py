"""Background task runner with progress replay for SSE subscribers.

Analysis runs as ``asyncio.create_task`` so it survives SSE disconnections.
Clients subscribe via a queue and receive replayed + live progress events.
Finished tasks are kept for ``RETENTION_SECONDS`` to allow reconnection.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, AsyncGenerator

from video_split.schemas import ProgressEvent
from video_split.service.error_text import describe_error
from video_split.service.video_service import DurationLimitExceeded
from video_split.service.xiaoyuzhou import XiaoyuzhouError

logger = logging.getLogger(__name__)

RETENTION_SECONDS = 300


@dataclass
class RunningTask:
    task_id: int
    user_id: int
    platform: str
    url: str
    events: list[dict[str, Any]] = field(default_factory=list)
    subscribers: list[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=list)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    confirm_event: asyncio.Event = field(default_factory=asyncio.Event)
    finished: bool = False
    last_progress: dict[str, Any] | None = None
    _bg_task: asyncio.Task[None] | None = field(default=None, repr=False)


class TaskRunner:

    def __init__(self) -> None:
        self._tasks: dict[int, RunningTask] = {}
        self._listeners: list[Callable[[RunningTask, dict[str, Any]], None]] = []

    def add_event_listener(self, cb: Callable[[RunningTask, dict[str, Any]], None]) -> None:
        if cb not in self._listeners:
            self._listeners.append(cb)

    def get(self, task_id: int) -> RunningTask | None:
        return self._tasks.get(task_id)

    def active_for_user(self, user_id: int) -> list[RunningTask]:
        return [t for t in self._tasks.values() if t.user_id == user_id]

    def is_running(self, task_id: int) -> bool:
        rt = self._tasks.get(task_id)
        return rt is not None and not rt.finished

    def _broadcast(self, rt: RunningTask, entry: dict[str, Any]) -> None:
        rt.events.append(entry)
        rt.last_progress = entry
        for q in list(rt.subscribers):
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                pass
        for cb in list(self._listeners):
            try:
                cb(rt, entry)
            except Exception:
                logger.exception("[runner] event listener failed")

    def start(
        self,
        task_id: int,
        user_id: int,
        platform: str,
        url: str,
        gen_factory: Callable[
            [asyncio.Event, asyncio.Event],
            AsyncGenerator[ProgressEvent, None],
        ],
    ) -> RunningTask:
        existing = self._tasks.get(task_id)
        if existing and not existing.finished:
            return existing

        rt = RunningTask(task_id=task_id, user_id=user_id, platform=platform, url=url)
        self._tasks[task_id] = rt

        async def _run() -> None:
            try:
                gen = gen_factory(rt.cancel_event, rt.confirm_event)
                async for event in gen:
                    entry = {"event": event.stage, "data": event.model_dump_json()}
                    self._broadcast(rt, entry)
            except XiaoyuzhouError as e:
                # Typed xiaoyuzhou failure — surface ``error_code`` so the
                # frontend can map a per-code friendly message + retry hint.
                logger.error("[runner] Task #%d xiaoyuzhou failure (%s): %s",
                             task_id, e.code, e)
                err = ProgressEvent(
                    stage="error", progress=0, message=describe_error(e),
                    detail={"error_code": e.code},
                )
                self._broadcast(rt, {"event": "error", "data": err.model_dump_json()})
            except DurationLimitExceeded as e:
                # Deterministic — same URL will always exceed the limit, so
                # tell the frontend not to offer a retry.
                logger.error("[runner] Task #%d duration exceeded: %s", task_id, e)
                err = ProgressEvent(
                    stage="error", progress=0, message=describe_error(e),
                    detail={"error_code": "duration_exceeded"},
                )
                self._broadcast(rt, {"event": "error", "data": err.model_dump_json()})
            except Exception as e:
                logger.error("[runner] Task #%d unhandled: %s", task_id, e)
                err = ProgressEvent(stage="error", progress=0, message=describe_error(e))
                self._broadcast(rt, {"event": "error", "data": err.model_dump_json()})
            finally:
                rt.finished = True
                for q in list(rt.subscribers):
                    try:
                        q.put_nowait(None)
                    except asyncio.QueueFull:
                        pass
                await asyncio.sleep(RETENTION_SECONDS)
                self._tasks.pop(task_id, None)

        rt._bg_task = asyncio.create_task(_run())
        return rt

    def subscribe(
        self, task_id: int
    ) -> tuple[list[dict[str, Any]], asyncio.Queue[dict[str, Any] | None]] | None:
        rt = self._tasks.get(task_id)
        if rt is None:
            return None
        q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=500)
        if not rt.finished:
            rt.subscribers.append(q)
        else:
            q.put_nowait(None)
        return list(rt.events), q

    def unsubscribe(self, task_id: int, q: asyncio.Queue[dict[str, Any] | None]) -> None:
        rt = self._tasks.get(task_id)
        if rt:
            try:
                rt.subscribers.remove(q)
            except ValueError:
                pass

    def cancel(self, task_id: int) -> bool:
        rt = self._tasks.get(task_id)
        if rt and not rt.finished:
            rt.cancel_event.set()
            return True
        return False

    def confirm(self, task_id: int) -> bool:
        rt = self._tasks.get(task_id)
        if rt and not rt.finished:
            rt.confirm_event.set()
            return True
        return False

    def remove(self, task_id: int) -> None:
        """Immediately evict a task from the in-memory registry."""
        rt = self._tasks.pop(task_id, None)
        if rt and rt._bg_task and not rt._bg_task.done():
            rt.cancel_event.set()
            rt._bg_task.cancel()


runner = TaskRunner()
