"""Shared LLM chat-completions POST with retry.

Retries transient failures — 429 (rate limit), 5xx, and connection/read
timeouts — with exponential backoff. 429 matters most in practice: BigModel
rate-limits after transcription has already succeeded, and failing the task
there wastes the expensive ASR step; a short backoff usually rides out the
limit window.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

_TRANSIENT_ERRORS = (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError)
_MAX_RETRIES = 3


def _retryable_status(code: int) -> bool:
    return code == 429 or code >= 500


async def post_chat(
    url: str,
    payload: dict,
    headers: dict[str, str],
    timeout: httpx.Timeout,
) -> dict:
    """POST a chat/completions payload, retrying 429/5xx/transient errors.

    Backoff: 2^attempt seconds (2s, 4s), max 3 attempts. Raises the last
    error when retries are exhausted; non-retryable HTTP errors raise
    immediately.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if _retryable_status(code) and attempt < _MAX_RETRIES:
                wait = 2 ** attempt
                logger.warning("[llm] HTTP %d (attempt %d/%d), retrying in %ds",
                               code, attempt, _MAX_RETRIES, wait)
                await asyncio.sleep(wait)
                last_exc = e
                continue
            logger.error("[llm] HTTP %d from %s: %s", code, url, e.response.text[:500])
            raise
        except _TRANSIENT_ERRORS as e:
            last_exc = e
            if attempt < _MAX_RETRIES:
                wait = 2 ** attempt
                logger.warning("[llm] %s (attempt %d/%d), retrying in %ds",
                               type(e).__name__, attempt, _MAX_RETRIES, wait)
                await asyncio.sleep(wait)
                continue
            logger.exception("[llm] Request to %s failed after %d attempts", url, _MAX_RETRIES)
            raise
        except Exception:
            logger.exception("[llm] Request to %s failed", url)
            raise
    raise last_exc  # type: ignore[misc]  # unreachable in practice
