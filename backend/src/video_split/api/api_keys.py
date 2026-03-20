from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from video_split.database import get_db
from video_split.dependencies import require_user
from video_split.models import ApiKey, User

router = APIRouter(tags=["api-keys"])

KEY_PREFIX = "vsk_"
KEY_BYTES = 24


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class ApiKeyOut(BaseModel):
    id: int
    name: str
    key_prefix: str
    is_active: bool
    last_used_at: str | None = None
    created_at: str

    model_config = {"from_attributes": True}


class ApiKeyCreated(ApiKeyOut):
    full_key: str


def _generate_key() -> str:
    return KEY_PREFIX + secrets.token_hex(KEY_BYTES)


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@router.get("/api/settings/api-keys", response_model=list[ApiKeyOut])
@router.get("/api/settings/tokens", response_model=list[ApiKeyOut])
async def list_api_keys(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()
    return [
        ApiKeyOut(
            id=k.id, name=k.name, key_prefix=k.key_prefix, is_active=k.is_active,
            last_used_at=k.last_used_at.isoformat() if k.last_used_at else None,
            created_at=k.created_at.isoformat() if k.created_at else "",
        )
        for k in keys
    ]


@router.post("/api/settings/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
@router.post("/api/settings/tokens", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: ApiKeyCreate,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    full_key = _generate_key()
    ak = ApiKey(
        user_id=user.id,
        name=body.name,
        key_hash=_hash_key(full_key),
        key_prefix=full_key[:12] + "...",
    )
    db.add(ak)
    await db.commit()
    await db.refresh(ak)
    return ApiKeyCreated(
        id=ak.id, name=ak.name, key_prefix=ak.key_prefix, is_active=ak.is_active,
        last_used_at=None,
        created_at=ak.created_at.isoformat() if ak.created_at else "",
        full_key=full_key,
    )


@router.delete("/api/settings/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/api/settings/tokens/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    key_id: int,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user.id)
    )
    ak = result.scalar_one_or_none()
    if ak is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    await db.delete(ak)
    await db.commit()
