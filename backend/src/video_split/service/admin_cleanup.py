from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from video_split.config import get_settings
from video_split.models import Task, User, Video
from video_split.service.data_sync import EXPORTS_DIR, _export_filename

logger = logging.getLogger(__name__)


@dataclass
class UserDeletionPlan:
    user: User
    library_videos: int
    public_videos: int
    private_videos: int
    task_count: int
    api_token_count: int
    export_paths: set[Path]
    thumbnail_paths: set[Path]
    temp_dirs: set[Path]

    @property
    def total_items(self) -> int:
        return len(self.export_paths) + len(self.thumbnail_paths) + len(self.temp_dirs)


@dataclass
class OrphanCleanupPlan:
    export_paths: set[Path]
    thumbnail_paths: set[Path]
    task_dirs: set[Path]

    @property
    def total_items(self) -> int:
        return len(self.export_paths) + len(self.thumbnail_paths) + len(self.task_dirs)


@dataclass
class CleanupExecutionResult:
    removed_exports: int
    removed_thumbnails: int
    removed_task_dirs: int
    errors: list[str]

    @property
    def removed_total(self) -> int:
        return self.removed_exports + self.removed_thumbnails + self.removed_task_dirs


def _thumbnail_dir() -> Path:
    settings = get_settings()
    return Path(settings.storage.temp_dir).parent / "thumbnails"


def _local_thumbnail_path(thumbnail_url: str) -> Path | None:
    prefix = "/api/thumbnails/"
    if not thumbnail_url.startswith(prefix):
        return None
    filename = thumbnail_url[len(prefix):]
    if not filename:
        return None
    return _thumbnail_dir() / filename


async def build_user_deletion_plan(db: AsyncSession, user_id: int) -> UserDeletionPlan | None:
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.videos),
            selectinload(User.tasks),
            selectinload(User.api_keys),
        )
        .where(User.id == user_id, User.role != "admin")
    )
    user = result.scalar_one_or_none()
    if user is None:
        return None

    videos = list(user.videos)
    tasks = list(user.tasks)

    export_paths: set[Path] = set()
    for platform, video_id in {(video.platform, video.video_id) for video in videos}:
        other_result = await db.execute(
            select(Video.id).where(
                Video.platform == platform,
                Video.video_id == video_id,
                Video.user_id != user_id,
            ).limit(1)
        )
        if other_result.scalar_one_or_none() is None:
            export_path = EXPORTS_DIR / _export_filename(platform, video_id)
            if export_path.exists():
                export_paths.add(export_path)

    thumbnail_paths: set[Path] = set()
    for thumbnail_url in {video.thumbnail_url for video in videos if video.thumbnail_url}:
        thumb_path = _local_thumbnail_path(thumbnail_url)
        if thumb_path is None:
            continue
        other_result = await db.execute(
            select(Video.id).where(
                Video.thumbnail_url == thumbnail_url,
                Video.user_id != user_id,
            ).limit(1)
        )
        if other_result.scalar_one_or_none() is None and thumb_path.exists():
            thumbnail_paths.add(thumb_path)

    temp_dirs = {
        Path(task.temp_dir) for task in tasks
        if task.temp_dir and Path(task.temp_dir).exists()
    }

    library_videos = len(videos)
    public_videos = sum(1 for video in videos if video.is_public)

    return UserDeletionPlan(
        user=user,
        library_videos=library_videos,
        public_videos=public_videos,
        private_videos=library_videos - public_videos,
        task_count=len(tasks),
        api_token_count=len(user.api_keys),
        export_paths=export_paths,
        thumbnail_paths=thumbnail_paths,
        temp_dirs=temp_dirs,
    )


async def build_orphan_cleanup_plan(db: AsyncSession) -> OrphanCleanupPlan:
    video_rows = (await db.execute(select(Video.platform, Video.video_id, Video.thumbnail_url))).all()
    expected_exports = {
        _export_filename(platform, video_id)
        for platform, video_id, _thumbnail_url in video_rows
    }
    expected_thumbnails = {
        local_path.name
        for _platform, _video_id, thumbnail_url in video_rows
        for local_path in [_local_thumbnail_path(thumbnail_url or "")]
        if local_path is not None
    }

    task_rows = await db.execute(select(Task.temp_dir))
    expected_task_dirs = {
        Path(temp_dir).name
        for (temp_dir,) in task_rows.all()
        if temp_dir and Path(temp_dir).name.isdigit()
    }

    export_paths = {
        path for path in EXPORTS_DIR.glob("*.json")
        if path.name not in expected_exports
    } if EXPORTS_DIR.exists() else set()

    thumbnail_dir = _thumbnail_dir()
    thumbnail_paths = {
        path for path in thumbnail_dir.iterdir()
        if path.is_file() and path.name not in expected_thumbnails
    } if thumbnail_dir.exists() else set()

    temp_root = Path(get_settings().storage.temp_dir)
    task_dirs = {
        path for path in temp_root.iterdir()
        if path.is_dir() and path.name.isdigit() and path.name not in expected_task_dirs
    } if temp_root.exists() else set()

    return OrphanCleanupPlan(
        export_paths=export_paths,
        thumbnail_paths=thumbnail_paths,
        task_dirs=task_dirs,
    )


def _remove_paths(*, files: set[Path], dirs: set[Path]) -> tuple[int, int, list[str]]:
    removed_files = 0
    removed_dirs = 0
    errors: list[str] = []

    for path in sorted(files):
        try:
            path.unlink(missing_ok=True)
            removed_files += 1
        except OSError as exc:
            logger.warning("[cleanup] Failed to remove file %s: %s", path, exc)
            errors.append(f"{path}: {exc}")

    for path in sorted(dirs):
        try:
            shutil.rmtree(path, ignore_errors=False)
            removed_dirs += 1
        except OSError as exc:
            logger.warning("[cleanup] Failed to remove directory %s: %s", path, exc)
            errors.append(f"{path}: {exc}")

    return removed_files, removed_dirs, errors


def execute_user_deletion_cleanup(plan: UserDeletionPlan) -> CleanupExecutionResult:
    removed_exports, removed_temp_dirs, errors = _remove_paths(
        files=plan.export_paths,
        dirs=plan.temp_dirs,
    )
    removed_thumbnails, _unused_dir_count, thumb_errors = _remove_paths(
        files=plan.thumbnail_paths,
        dirs=set(),
    )
    return CleanupExecutionResult(
        removed_exports=removed_exports,
        removed_thumbnails=removed_thumbnails,
        removed_task_dirs=removed_temp_dirs,
        errors=errors + thumb_errors,
    )


def execute_orphan_cleanup(plan: OrphanCleanupPlan) -> CleanupExecutionResult:
    removed_exports, removed_task_dirs, errors = _remove_paths(
        files=plan.export_paths,
        dirs=plan.task_dirs,
    )
    removed_thumbnails, _unused_dir_count, thumb_errors = _remove_paths(
        files=plan.thumbnail_paths,
        dirs=set(),
    )
    return CleanupExecutionResult(
        removed_exports=removed_exports,
        removed_thumbnails=removed_thumbnails,
        removed_task_dirs=removed_task_dirs,
        errors=errors + thumb_errors,
    )
