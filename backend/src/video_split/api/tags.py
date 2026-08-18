from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from video_split.database import get_db
from video_split.dependencies import get_current_user, require_admin
from video_split.models import Tag, User, Video
from video_split.schemas import TagCreate, TagOut, TagUpdate

router = APIRouter(prefix="/api/tags", tags=["tags"])


@router.get("", response_model=list[TagOut])
async def list_tags(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Tag).order_by(Tag.name))
    return list(result.scalars().all())


@router.post("", response_model=TagOut, status_code=status.HTTP_201_CREATED)
async def create_tag(
    body: TagCreate,
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(Tag).where(Tag.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Tag already exists")
    tag = Tag(name=body.name, color=body.color)
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag


@router.put("/{tag_id}", response_model=TagOut)
async def update_tag(
    tag_id: int,
    body: TagUpdate,
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Tag).where(Tag.id == tag_id))
    tag = result.scalar_one_or_none()
    if tag is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tag not found")
    if body.name is not None:
        tag.name = body.name
    if body.color is not None:
        tag.color = body.color
    await db.commit()
    await db.refresh(tag)
    return tag


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: int,
    _user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Tag).where(Tag.id == tag_id))
    tag = result.scalar_one_or_none()
    if tag is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tag not found")
    await db.delete(tag)
    await db.commit()


@router.post("/{video_id}/tags", response_model=list[TagOut])
async def add_video_tag(
    video_id: int,
    body: TagCreate,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Video).options(selectinload(Video.tags))
        .where(Video.id == video_id, Video.user_id == user.id)
    )
    video = result.scalar_one_or_none()
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")

    tag_result = await db.execute(select(Tag).where(Tag.name == body.name))
    tag = tag_result.scalar_one_or_none()
    if tag is None:
        tag = Tag(name=body.name, color=body.color)
        db.add(tag)
        await db.flush()

    if tag not in video.tags:
        video.tags.append(tag)
    await db.commit()
    return [TagOut.model_validate(t) for t in video.tags]


@router.delete("/{video_id}/tags/{tag_id}", response_model=list[TagOut])
async def remove_video_tag(
    video_id: int,
    tag_id: int,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Video).options(selectinload(Video.tags))
        .where(Video.id == video_id, Video.user_id == user.id)
    )
    video = result.scalar_one_or_none()
    if video is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Video not found")

    tag_result = await db.execute(select(Tag).where(Tag.id == tag_id))
    tag = tag_result.scalar_one_or_none()
    if tag and tag in video.tags:
        video.tags.remove(tag)
    await db.commit()
    return [TagOut.model_validate(t) for t in video.tags]
