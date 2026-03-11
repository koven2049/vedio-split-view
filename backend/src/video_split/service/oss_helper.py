"""Aliyun OSS helper for uploading audio files and generating signed URLs."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

import oss2
from oss2.models import LifecycleRule, LifecycleExpiration, BucketLifecycle

from video_split.config import get_settings

logger = logging.getLogger(__name__)

_lifecycle_ensured = False


def _get_bucket() -> oss2.Bucket:
    cfg = get_settings().oss
    auth = oss2.Auth(cfg.access_key_id, cfg.access_key_secret)
    return oss2.Bucket(auth, cfg.endpoint, cfg.bucket_name)


def _ensure_lifecycle() -> None:
    """Ensure a lifecycle rule exists for the configured prefix (idempotent)."""
    global _lifecycle_ensured
    if _lifecycle_ensured:
        return

    cfg = get_settings().oss
    bucket = _get_bucket()
    prefix = cfg.prefix.rstrip("/") + "/"
    rule_id = f"vsplit-cleanup-{prefix.replace('/', '-').strip('-')}"

    try:
        existing = bucket.get_bucket_lifecycle()
        for rule in existing.rules:
            if rule.id == rule_id:
                _lifecycle_ensured = True
                return
        rules = list(existing.rules)
    except oss2.exceptions.NoSuchLifecycle:
        rules = []

    rules.append(LifecycleRule(
        id=rule_id,
        prefix=prefix,
        status=LifecycleRule.ENABLED,
        expiration=LifecycleExpiration(days=cfg.object_expiry_days),
    ))
    bucket.put_bucket_lifecycle(BucketLifecycle(rules))
    logger.info(
        "OSS lifecycle rule '%s' set: prefix=%s, expiry=%d day(s)",
        rule_id, prefix, cfg.object_expiry_days,
    )
    _lifecycle_ensured = True


def upload_and_sign(local_path: Path) -> tuple[str, str]:
    """Upload a local file to OSS and return (object_key, signed_url).

    The signed URL is valid for ``oss.sign_expiry_seconds`` (default 1h).
    On first call, also ensures a lifecycle rule is in place as a safety net.
    """
    _ensure_lifecycle()

    cfg = get_settings().oss
    bucket = _get_bucket()

    suffix = local_path.suffix
    object_key = f"{cfg.prefix}/{uuid.uuid4().hex}{suffix}"

    size_mb = local_path.stat().st_size / (1024 * 1024)
    logger.info("[oss] Uploading %s (%.1f MB) → %s", local_path.name, size_mb, object_key)
    bucket.put_object_from_file(object_key, str(local_path))

    signed_url = bucket.sign_url("GET", object_key, cfg.sign_expiry_seconds)
    logger.info("[oss] Signed URL generated (expiry=%ds)", cfg.sign_expiry_seconds)
    return object_key, signed_url


def delete_object(object_key: str) -> None:
    """Delete a single object from OSS. Ignores errors."""
    try:
        bucket = _get_bucket()
        bucket.delete_object(object_key)
        logger.info("[oss] Deleted %s", object_key)
    except Exception:
        logger.warning("[oss] Failed to delete %s", object_key, exc_info=True)
