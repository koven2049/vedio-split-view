from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from video_split.database import get_db
from video_split.dependencies import require_admin
from video_split.models import User, Video
from video_split.schemas import (
    AdminCleanupResultOut,
    AdminCleanupSummaryOut,
    AdminCreateUser,
    AdminUserDeletePreviewOut,
    UserInfo,
)
from video_split.service.auth_service import hash_password
from video_split.service.admin_cleanup import (
    build_orphan_cleanup_plan,
    build_user_deletion_plan,
    execute_orphan_cleanup,
    execute_user_deletion_cleanup,
)
from video_split.service.data_sync import export_all_videos, import_videos

router = APIRouter(prefix="/api/admin", tags=["admin"])


class UserAdminInfo(UserInfo):
    video_count: int = 0


@router.get("/users", response_model=list[UserAdminInfo])
async def list_users(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.role != "admin").order_by(User.created_at))
    users = result.scalars().all()
    out = []
    for u in users:
        count_result = await db.execute(
            select(func.count()).select_from(Video).where(Video.user_id == u.id)
        )
        count = count_result.scalar() or 0
        info = UserAdminInfo.model_validate(u)
        info.video_count = count
        out.append(info)
    return out


@router.post("/users", response_model=UserInfo, status_code=status.HTTP_201_CREATED)
async def create_user(
    req: AdminCreateUser,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(User).where(User.username == req.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")
    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        role=req.role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.put("/users/{user_id}/toggle", response_model=UserInfo)
async def toggle_user(
    user_id: int,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id, User.role != "admin"))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user.is_active = not user.is_active
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    plan = await build_user_deletion_plan(db, user_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    await db.delete(plan.user)
    await db.commit()
    execute_user_deletion_cleanup(plan)


@router.get("/users/{user_id}/delete-preview", response_model=AdminUserDeletePreviewOut)
async def delete_user_preview(
    user_id: int,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    plan = await build_user_deletion_plan(db, user_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    return AdminUserDeletePreviewOut(
        user_id=plan.user.id,
        username=plan.user.username,
        library_videos=plan.library_videos,
        public_videos=plan.public_videos,
        private_videos=plan.private_videos,
        task_count=plan.task_count,
        api_token_count=plan.api_token_count,
        export_files=len(plan.export_paths),
        thumbnail_files=len(plan.thumbnail_paths),
        temp_dirs=len(plan.temp_dirs),
        total_items=plan.total_items,
    )


@router.get("/cleanup/summary", response_model=AdminCleanupSummaryOut)
async def admin_cleanup_summary(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    plan = await build_orphan_cleanup_plan(db)
    return AdminCleanupSummaryOut(
        orphan_exports=len(plan.export_paths),
        orphan_thumbnails=len(plan.thumbnail_paths),
        orphan_task_dirs=len(plan.task_dirs),
        total_items=plan.total_items,
    )


@router.post("/cleanup/run", response_model=AdminCleanupResultOut)
async def admin_cleanup_run(
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    plan = await build_orphan_cleanup_plan(db)
    result = execute_orphan_cleanup(plan)
    return AdminCleanupResultOut(
        orphan_exports=len(plan.export_paths),
        orphan_thumbnails=len(plan.thumbnail_paths),
        orphan_task_dirs=len(plan.task_dirs),
        total_items=plan.total_items,
        removed_exports=result.removed_exports,
        removed_thumbnails=result.removed_thumbnails,
        removed_task_dirs=result.removed_task_dirs,
        removed_total=result.removed_total,
        errors=result.errors,
    )


@router.post("/export")
async def admin_export_all(
    platform: str = Query("", description="Filter by platform: youtube, bilibili, or empty for all"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Export videos to data/exports/ as JSON files."""
    count = await export_all_videos(db, platform=platform)
    return {"exported": count, "platform": platform or "all"}


@router.post("/import")
async def admin_import_videos(
    target_username: str = Query(..., description="Import videos into this user's library"),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Import videos from data/exports/ into the specified user's library (incremental)."""
    result = await db.execute(select(User).where(User.username == target_username))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"User '{target_username}' not found")
    stats = await import_videos(db, user.id)
    return stats
