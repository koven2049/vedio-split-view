from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from video_split.database import get_db
from video_split.dependencies import get_current_user
from video_split.models import User
from video_split.schemas import AuthLogin, LangUpdate, TokenResponse, UserInfo, UserPreferences
from video_split.service.auth_service import (
    PendingApprovalError,
    authenticate_user,
    create_token,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: AuthLogin, db: AsyncSession = Depends(get_db)):
    try:
        user = await authenticate_user(db, body.username, body.password)
    except PendingApprovalError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your account is pending admin approval.")
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    token = create_token(user.id, user.role)
    return TokenResponse(
        access_token=token, role=user.role, username=user.username,
        lang_preference=user.lang_preference,
    )


@router.get("/me", response_model=UserInfo)
async def me(user: User = Depends(get_current_user)):
    return user


@router.put("/lang")
async def update_lang(
    body: LangUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user.lang_preference = body.lang
    await db.commit()
    return {"lang_preference": user.lang_preference}


@router.get("/preferences")
async def get_preferences(user: User = Depends(get_current_user)):
    from video_split.config import get_settings
    settings = get_settings()
    prefs = {}
    try:
        prefs = json.loads(user.preferences_json or "{}")
    except (json.JSONDecodeError, TypeError):
        pass
    return {
        "max_duration_seconds": prefs.get("max_duration_seconds") or settings.video.max_duration_seconds,
        "confirm_threshold_seconds": prefs.get("confirm_threshold_seconds") or settings.video.confirm_threshold_seconds,
        "max_concurrent_analyses": prefs.get("max_concurrent_analyses") or settings.storage.max_pending_tasks_per_user,
        "defaults": {
            "max_duration_seconds": settings.video.max_duration_seconds,
            "confirm_threshold_seconds": settings.video.confirm_threshold_seconds,
            "max_concurrent_analyses": settings.storage.max_pending_tasks_per_user,
        },
    }


@router.put("/preferences")
async def update_preferences(
    body: UserPreferences,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    prefs = {}
    if body.max_duration_seconds > 0:
        prefs["max_duration_seconds"] = body.max_duration_seconds
    if body.confirm_threshold_seconds > 0:
        prefs["confirm_threshold_seconds"] = body.confirm_threshold_seconds
    if body.max_concurrent_analyses > 0:
        prefs["max_concurrent_analyses"] = body.max_concurrent_analyses
    user.preferences_json = json.dumps(prefs)
    await db.commit()
    return await get_preferences(user)


@router.get("/usage-stats")
async def get_usage_stats(user: User = Depends(get_current_user)):
    """Return cumulative per-model usage stats for this user."""
    try:
        stats = json.loads(user.usage_stats_json or "{}")
    except (json.JSONDecodeError, TypeError):
        stats = {}
    return stats
