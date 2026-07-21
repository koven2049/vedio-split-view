from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from video_split.database import Base

video_tags = Table(
    "video_tags",
    Base.metadata,
    Column("video_id", Integer, ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    # Two roles only: "admin" (single seeded account, full access) and
    # "viewer" (read-only, sees the entire library).
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    lang_preference: Mapped[str] = mapped_column(String(4), default="zh")
    preferences_json: Mapped[str] = mapped_column(Text, default="{}")
    usage_stats_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    videos: Mapped[list[Video]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    tasks: Mapped[list[Task]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    bilibili_credential: Mapped[BilibiliCredential | None] = relationship(
        back_populates="owner", uselist=False, cascade="all, delete-orphan"
    )


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(1024))
    platform: Mapped[str] = mapped_column(String(32))
    video_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(512), default="")
    thumbnail_url: Mapped[str] = mapped_column(String(1024), default="")
    upload_date: Mapped[str] = mapped_column(String(16), default="")
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str] = mapped_column(Text, default="")
    summary_en: Mapped[str] = mapped_column(Text, default="")
    raw_transcript: Mapped[str] = mapped_column(Text, default="")
    subtitle_json: Mapped[str] = mapped_column(Text, default="")
    usage_json: Mapped[str] = mapped_column(Text, default="")
    mindmap_json: Mapped[str] = mapped_column(Text, default="")
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped[User] = relationship(back_populates="videos")
    segments: Mapped[list[Segment]] = relationship(
        back_populates="video", cascade="all, delete-orphan", order_by="Segment.segment_index"
    )
    tags: Mapped[list[Tag]] = relationship(secondary=video_tags, back_populates="videos")


class Segment(Base):
    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    segment_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(256), default="")
    title_en: Mapped[str] = mapped_column(String(256), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    summary_en: Mapped[str] = mapped_column(Text, default="")
    start_seconds: Mapped[int] = mapped_column(Integer, default=0)
    end_seconds: Mapped[int] = mapped_column(Integer, default=0)

    video: Mapped[Video] = relationship(back_populates="segments")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    color: Mapped[str] = mapped_column(String(16), default="#3b82f6")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    videos: Mapped[list[Video]] = relationship(secondary=video_tags, back_populates="tags")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(1024))
    platform: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="downloading")
    video_title: Mapped[str] = mapped_column(String(512), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    temp_dir: Mapped[str] = mapped_column(String(512), default="")
    progress_data: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped[User] = relationship(back_populates="tasks")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    key_hash: Mapped[str] = mapped_column(String(128), unique=True)
    key_prefix: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    owner: Mapped[User] = relationship(back_populates="api_keys")


class BilibiliCredential(Base):
    __tablename__ = "bilibili_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    sessdata: Mapped[str] = mapped_column(String(256), default="")
    bili_jct: Mapped[str] = mapped_column(String(256), default="")
    buvid3: Mapped[str] = mapped_column(String(256), default="")
    bilibili_username: Mapped[str] = mapped_column(String(128), default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    owner: Mapped[User] = relationship(back_populates="bilibili_credential")
