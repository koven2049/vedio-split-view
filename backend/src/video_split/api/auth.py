from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from video_split.database import get_db
from video_split.dependencies import get_current_user
from video_split.models import User
from video_split.schemas import AuthLogin, LangUpdate, TokenResponse, UserInfo
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
