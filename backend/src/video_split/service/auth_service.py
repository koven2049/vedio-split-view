from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from video_split.config import get_settings
from video_split.models import User

TOKEN_EXPIRE_HOURS = 72


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: int, role: str) -> str:
    settings = get_settings()
    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, settings.app.secret_key, algorithm="HS256")


def decode_token(token: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.app.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


async def ensure_admin_user(db: AsyncSession) -> None:
    """Create or update admin user from config. Called on startup."""
    settings = get_settings()
    result = await db.execute(select(User).where(User.username == "admin"))
    admin = result.scalar_one_or_none()
    new_hash = hash_password(settings.admin.password)
    if admin is None:
        admin = User(username="admin", password_hash=new_hash, role="admin", is_active=True)
        db.add(admin)
    else:
        admin.password_hash = new_hash
        admin.role = "admin"
        admin.is_active = True
    await db.commit()


class PendingApprovalError(Exception):
    pass


async def authenticate_user(db: AsyncSession, username: str, password: str) -> User:
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise ValueError("Invalid credentials")
    if not user.is_active:
        raise PendingApprovalError("Your account is pending admin approval.")
    return user
