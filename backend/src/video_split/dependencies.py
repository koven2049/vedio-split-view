from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from video_split.database import get_db
from video_split.models import ApiKey, User
from video_split.service.auth_service import decode_token

_bearer = HTTPBearer(auto_error=False)


async def _resolve_api_key(api_key: str, db: AsyncSession) -> User | None:
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    result = await db.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active == True)  # noqa: E712
    )
    ak = result.scalar_one_or_none()
    if ak is None:
        return None
    ak.last_used_at = datetime.now(timezone.utc)
    await db.commit()
    result = await db.execute(select(User).where(User.id == ak.user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> User:
    if x_api_key:
        user = await _resolve_api_key(x_api_key, db)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")
        return user

    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    payload = decode_token(creds.credentials)
    if payload is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user_id = int(payload.get("sub", 0))
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or disabled")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Write access. Only the single admin account may mutate content."""
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user


async def require_authenticated(user: User = Depends(get_current_user)) -> User:
    """Read access. Any logged-in account (admin or viewer) is allowed."""
    return user
