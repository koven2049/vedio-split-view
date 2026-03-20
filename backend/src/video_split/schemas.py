from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AuthRegister(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=4, max_length=128)


class AuthLogin(BaseModel):
    username: str
    password: str


class AdminCreateUser(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=4, max_length=128)
    role: str = Field(default="user", pattern=r"^(user|viewer)$")


class LangUpdate(BaseModel):
    lang: str = Field(pattern=r"^(zh|en)$")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    username: str
    lang_preference: str = "zh"


class UserInfo(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    lang_preference: str = "zh"
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminUserDeletePreviewOut(BaseModel):
    user_id: int
    username: str
    library_videos: int
    public_videos: int
    private_videos: int
    task_count: int
    api_token_count: int
    export_files: int
    thumbnail_files: int
    temp_dirs: int
    total_items: int


class AdminCleanupSummaryOut(BaseModel):
    orphan_exports: int
    orphan_thumbnails: int
    orphan_task_dirs: int
    total_items: int


class AdminCleanupResultOut(AdminCleanupSummaryOut):
    removed_exports: int
    removed_thumbnails: int
    removed_task_dirs: int
    removed_total: int
    errors: list[str] = []


class AnalyzeRequest(BaseModel):
    url: str = Field(min_length=5, max_length=1024)


class ConfirmRequest(BaseModel):
    task_id: int


class SegmentOut(BaseModel):
    id: int
    segment_index: int
    title: str
    title_en: str = ""
    summary: str
    summary_en: str = ""
    start_seconds: int
    end_seconds: int

    model_config = {"from_attributes": True}


class TagOut(BaseModel):
    id: int
    name: str
    color: str

    model_config = {"from_attributes": True}


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    color: str = Field(default="#3b82f6", max_length=16)


class TagUpdate(BaseModel):
    name: str | None = None
    color: str | None = None


class VideoOut(BaseModel):
    id: int
    url: str
    platform: str
    video_id: str
    title: str
    thumbnail_url: str
    upload_date: str = ""
    duration_seconds: int
    summary: str
    summary_en: str = ""
    usage_json: str = ""
    is_public: bool
    created_at: datetime
    updated_at: datetime
    segments: list[SegmentOut] = []
    tags: list[TagOut] = []
    owner_name: str = ""

    model_config = {"from_attributes": True}


class VideoListOut(BaseModel):
    id: int
    url: str
    platform: str
    title: str
    thumbnail_url: str
    duration_seconds: int
    is_public: bool
    created_at: datetime
    tags: list[TagOut] = []
    owner_name: str = ""

    model_config = {"from_attributes": True}


class VideoUpdate(BaseModel):
    title: str | None = None
    summary: str | None = None


class TaskOut(BaseModel):
    id: int
    url: str
    platform: str
    status: str
    video_title: str
    error_message: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BilibiliStatusOut(BaseModel):
    connected: bool
    bilibili_username: str = ""
    expired: bool = False


class QRCodeOut(BaseModel):
    qr_key: str
    qr_url: str
    qr_image_base64: str


class PaginatedResponse(BaseModel):
    items: list
    total: int
    page: int
    page_size: int


class ProgressEvent(BaseModel):
    stage: str
    progress: float
    message: str
    detail: dict | None = None


# --- Debug API schemas ---


class DebugDownloadRequest(BaseModel):
    url: str = Field(min_length=5, max_length=1024)


class ChunkInfo(BaseModel):
    index: int
    filename: str
    path: str
    size_bytes: int
    duration_seconds: float


class DebugDownloadResponse(BaseModel):
    task_id: int
    platform: str
    title: str
    duration_seconds: int
    audio_path: str
    audio_size_bytes: int
    chunks: list[ChunkInfo]
    chunk_duration_config: int
    quota_used: int
    quota_max: int


class DebugTranscribeRequest(BaseModel):
    task_id: int
    chunk_index: int | None = None


class DebugTestASRRequest(BaseModel):
    file_path: str = Field(description="Local audio file path to transcribe")


class TranscriptSegment(BaseModel):
    start: float
    duration: float
    text: str


class DebugTranscribeResponse(BaseModel):
    task_id: int
    chunk_file: str | None = None
    segments: list[TranscriptSegment]
    total_segments: int


class DebugTaskInfo(BaseModel):
    task_id: int
    url: str
    platform: str
    status: str
    title: str
    audio_path: str | None = None
    audio_size_mb: float | None = None
    chunks: list[ChunkInfo] = []
    created_at: datetime


class DebugCleanupResponse(BaseModel):
    task_id: int
    files_removed: int
    status: str
