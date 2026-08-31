from __future__ import annotations

import sys
from pathlib import Path
from functools import lru_cache
from typing import Any

import yaml
from pydantic import BaseModel, field_validator


class AppConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    frontend_port: int = 5180
    secret_key: str = "change-me"

    @property
    def cors_origins(self) -> list[str]:
        return [
            f"https://localhost:{self.frontend_port}",
            f"http://localhost:{self.port}",
            "http://localhost:5173",
        ]


class AdminConfig(BaseModel):
    password: str = ""


class LLMConfig(BaseModel):
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    timeout_ms: int = 120000
    max_tokens: int = 8192


class TranscriptionConfig(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = "whisper-1"
    language: str = ""
    chunk_duration_seconds: int = 300


class NetworkConfig(BaseModel):
    proxy_enabled: bool = False
    http_proxy: str = ""
    https_proxy: str = ""
    youtube_cookies_file: str = ""
    download_timeout_seconds: int = 120

    @property
    def proxy_url(self) -> str | None:
        """Single proxy URL for httpx 0.28+ ``proxy=`` parameter."""
        if not self.proxy_enabled:
            return None
        return self.http_proxy or self.https_proxy or None


class OSSConfig(BaseModel):
    endpoint: str = ""
    access_key_id: str = ""
    access_key_secret: str = ""
    bucket_name: str = ""
    prefix: str = "video-split/tmp"
    sign_expiry_seconds: int = 3600
    object_expiry_days: int = 1

    @property
    def enabled(self) -> bool:
        return bool(self.endpoint and self.access_key_id and self.access_key_secret and self.bucket_name)


class BackupConfig(BaseModel):
    enabled: bool = False
    dir: str = ""
    max_copies: int = 3
    # cron-like schedule for launchd/cron: "daily" runs once at 03:00
    schedule: str = "daily"


class LLMBackupConfig(BaseModel):
    """Fallback LLM for content-filter-blocked chunks (e.g. GLM code 1301)."""
    enabled: bool = False
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    timeout_ms: int = 120000
    max_tokens: int = 8192


class LoggingConfig(BaseModel):
    level: str = "INFO"
    dir: str = "logs"
    max_file_size_mb: int = 50
    backup_count: int = 5


class StorageConfig(BaseModel):
    db_path: str = "data/video_split.db"
    temp_dir: str = "data/tmp"
    max_pending_tasks_per_user: int = 3
    max_total_videos_per_user: int = 100


class VideoConfig(BaseModel):
    max_duration_seconds: int = 12600
    confirm_threshold_seconds: int = 3600
    # Podcast (xiaoyuzhou) tends to be longer than short-form video; allow a
    # wider threshold before prompting the user to confirm a long upload.
    podcast_confirm_threshold_seconds: int = 7200
    min_segment_duration_seconds: int = 60


class FeishuConfig(BaseModel):
    """Non-secret Feishu bot settings. Credentials live in secrets_file."""

    enabled: bool = False
    result_base_url: str = ""
    allowed_open_ids: list[str] = []
    secrets_file: str = "feishu.yaml"


class Settings(BaseModel):
    app: AppConfig = AppConfig()
    admin: AdminConfig = AdminConfig()
    llm: LLMConfig = LLMConfig()
    transcription: TranscriptionConfig = TranscriptionConfig()
    oss: OSSConfig = OSSConfig()
    network: NetworkConfig = NetworkConfig()
    storage: StorageConfig = StorageConfig()
    video: VideoConfig = VideoConfig()
    backup: BackupConfig = BackupConfig()
    llm_backup: LLMBackupConfig = LLMBackupConfig()
    logging: LoggingConfig = LoggingConfig()
    feishu: FeishuConfig = FeishuConfig()

    @field_validator("admin")
    @classmethod
    def admin_password_required(cls, v: AdminConfig) -> AdminConfig:
        if not v.password:
            print("FATAL: admin.password must be set in config/app.yaml", file=sys.stderr)
            sys.exit(1)
        return v


_config_path_override: str | None = None


def set_config_path(path: str) -> None:
    global _config_path_override
    _config_path_override = path
    get_settings.cache_clear()


def _resolve_config_path() -> Path:
    if _config_path_override:
        return Path(_config_path_override)
    candidates = [
        Path("config/app.yaml"),
        Path(__file__).resolve().parents[3] / "config" / "app.yaml",
    ]
    for p in candidates:
        if p.exists():
            return p
    print("FATAL: config/app.yaml not found", file=sys.stderr)
    sys.exit(1)


@lru_cache
def get_settings() -> Settings:
    path = _resolve_config_path()
    with open(path) as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    return Settings(**data)
