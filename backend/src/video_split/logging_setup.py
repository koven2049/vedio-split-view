"""Centralized logging configuration.

Call ``setup_logging()`` once at application startup (before any
getLogger() calls produce output) to route all log messages to both
the console and a rotating file under the configured directory.
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from video_split.config import get_settings

_LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_BEIJING_TZ = timezone(timedelta(hours=8))


def _beijing_time(*_args: object) -> time.struct_time:
    """Convert UTC epoch to Beijing local time (UTC+8)."""
    return datetime.now(_BEIJING_TZ).timetuple()


def setup_logging() -> None:
    cfg = get_settings().logging
    log_dir = Path(cfg.dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, cfg.level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    if root.handlers:
        return

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
    formatter.converter = _beijing_time  # type: ignore[assignment]

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=cfg.max_file_size_mb * 1024 * 1024,
        backupCount=cfg.backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
