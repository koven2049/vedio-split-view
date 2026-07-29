from __future__ import annotations

import logging
from pathlib import Path
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from video_split.config import get_settings

logger = logging.getLogger(__name__)

_engine = None
_session_factory = None


class Base(DeclarativeBase):
    pass


def _get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        db_path = Path(settings.storage.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}",
            echo=False,
        )
    return _engine


def _get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(_get_engine(), expire_on_commit=False)
    return _session_factory


_MIGRATIONS: list[tuple[str, str, str]] = [
    ("videos", "summary_en", "TEXT DEFAULT ''"),
    ("videos", "subtitle_json", "TEXT DEFAULT ''"),
    ("segments", "title_en", "VARCHAR(256) DEFAULT ''"),
    ("segments", "summary_en", "TEXT DEFAULT ''"),
    ("videos", "usage_json", "TEXT DEFAULT ''"),
    ("videos", "mindmap_json", "TEXT DEFAULT ''"),
    ("videos", "upload_date", "VARCHAR(16) DEFAULT ''"),
    ("videos", "essence", "TEXT DEFAULT ''"),
    ("users", "lang_preference", "VARCHAR(4) DEFAULT 'zh'"),
    ("users", "preferences_json", "TEXT DEFAULT '{}'"),
    ("users", "usage_stats_json", "TEXT DEFAULT '{}'"),
]


async def _run_migrations(conn) -> None:  # type: ignore[no-untyped-def]
    """Add missing columns to existing tables (lightweight schema migration)."""
    for table, column, col_type in _MIGRATIONS:
        try:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            logger.info("[db] Added column %s.%s", table, column)
        except Exception:
            pass  # column already exists


_ORPHAN_RECOVERY = {
    "downloading": "failed_download",
    "transcribing": "failed_transcribe",
    "analyzing": "failed_analyze",
}


async def _recover_orphan_tasks(conn) -> None:  # type: ignore[no-untyped-def]
    """On startup, transition active tasks to retryable/failed states.

    After a restart no background process owns these tasks, so they
    must be marked failed so the user can retry them.
    """
    for active_status, target_status in _ORPHAN_RECOVERY.items():
        result = await conn.execute(
            text(
                "UPDATE tasks SET status = :target, error_message = :msg "
                "WHERE status = :active"
            ),
            {
                "target": target_status,
                "active": active_status,
                "msg": "Interrupted by server restart — please retry",
            },
        )
        if result.rowcount:
            logger.info("[db] Recovered %d orphan tasks: %s → %s", result.rowcount, active_status, target_status)


async def _migrate_user_role(conn) -> None:  # type: ignore[no-untyped-def]
    """Collapse the removed 'user' role into 'viewer'.

    The three-role model (admin/user/viewer) was reduced to two (admin/viewer).
    Any leftover 'user' rows become read-only viewers. admin is untouched.
    """
    result = await conn.execute(
        text("UPDATE users SET role = 'viewer' WHERE role = 'user'")
    )
    if result.rowcount:
        logger.info("[db] Migrated %d legacy 'user' accounts to 'viewer'", result.rowcount)


async def _ensure_platform_tags(conn) -> None:  # type: ignore[no-untyped-def]
    """Ensure every video has a platform tag (YouTube / Bilibili)."""
    for platform, name, color in [
        ("youtube", "YouTube", "#ff0000"),
        ("bilibili", "Bilibili", "#00a1d6"),
        ("xiaoyuzhou", "小宇宙", "#7c3aed"),
    ]:
        await conn.execute(
            text("INSERT OR IGNORE INTO tags (name, color) VALUES (:name, :color)"),
            {"name": name, "color": color},
        )
        await conn.execute(
            text(
                "INSERT OR IGNORE INTO video_tags (video_id, tag_id) "
                "SELECT v.id, t.id FROM videos v JOIN tags t ON t.name = :name "
                "WHERE v.platform = :platform "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM video_tags vt WHERE vt.video_id = v.id AND vt.tag_id = t.id"
                ")"
            ),
            {"name": name, "platform": platform},
        )


async def init_db() -> None:
    engine = _get_engine()
    async with engine.begin() as conn:
        from video_split.models import Base  # noqa: F811
        await conn.run_sync(Base.metadata.create_all)
        await _run_migrations(conn)
        await _migrate_user_role(conn)
        await _recover_orphan_tasks(conn)
        await _ensure_platform_tags(conn)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    factory = _get_session_factory()
    async with factory() as session:
        yield session
