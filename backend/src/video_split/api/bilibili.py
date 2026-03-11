from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from video_split.database import get_db
from video_split.dependencies import require_user
from video_split.models import BilibiliCredential, User
from video_split.schemas import BilibiliStatusOut, QRCodeOut
from video_split.service.bilibili_auth import generate_qr_code, poll_qr_status

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bilibili", tags=["bilibili"])


@router.post("/qr/generate", response_model=QRCodeOut)
async def generate_qr(
    _user: User = Depends(require_user),
):
    try:
        qr = await generate_qr_code()
    except Exception:
        logger.exception("[bilibili] QR code generation failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to generate Bilibili QR code")
    return QRCodeOut(qr_key=qr.qr_key, qr_url=qr.qr_url, qr_image_base64=qr.qr_image_base64)


@router.get("/qr/poll/{qr_key}")
async def poll_qr(
    qr_key: str,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await poll_qr_status(qr_key)

    if result.status == "confirmed" and result.sessdata:
        cred_result = await db.execute(
            select(BilibiliCredential).where(BilibiliCredential.user_id == user.id)
        )
        cred = cred_result.scalar_one_or_none()
        if cred is None:
            cred = BilibiliCredential(user_id=user.id)
            db.add(cred)
        cred.sessdata = result.sessdata
        cred.bili_jct = result.bili_jct
        cred.buvid3 = result.buvid3
        await db.commit()

    return {"status": result.status}


@router.get("/status", response_model=BilibiliStatusOut)
async def bilibili_status(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BilibiliCredential).where(BilibiliCredential.user_id == user.id)
    )
    cred = result.scalar_one_or_none()
    if cred is None or not cred.sessdata:
        return BilibiliStatusOut(connected=False)
    return BilibiliStatusOut(
        connected=True,
        bilibili_username=cred.bilibili_username,
    )


@router.delete("/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_bilibili(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BilibiliCredential).where(BilibiliCredential.user_id == user.id)
    )
    cred = result.scalar_one_or_none()
    if cred:
        await db.delete(cred)
        await db.commit()
