"""Shared LLM chat-completions POST with retry.

Retries transient failures — 429 (genuine rate limit), 5xx, and
connection/read timeouts — with exponential backoff.

**Not** retried: BigModel code 1113 (余额不足/资源包耗尽) and other
deterministic 429s whose body indicates a permanent account/quota state.
These fail fast with :class:`LLMInsufficientBalanceError` so the user sees a
clear "充值/换 key" message instead of burning backoff on requests that will
never succeed.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx

logger = logging.getLogger(__name__)

_TRANSIENT_ERRORS = (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError)
_MAX_RETRIES = 3

# BigModel error codes that mean "this account can't pay for the request right
# now" — deterministic, no point retrying within a single run.
_INSUFFICIENT_BALANCE_CODES = {"1113", "1112", "1128", "1129", "1301", "401", "403"}


class LLMInsufficientBalanceError(httpx.HTTPStatusError):
    """LLM account balance / quota exhausted (BigModel code 1113 etc.).

    Raised immediately (no retry) so callers can surface a clear
    "充值或更换 key" message. Subclasses HTTPStatusError so existing
    `except httpx.HTTPStatusError` handlers still catch it.
    """


def _retryable_status(code: int) -> bool:
    return code == 429 or code >= 500


def _parse_error_code(response: httpx.Response) -> str | None:
    """Extract BigModel-style error code from a 4xx/5xx body, if any."""
    try:
        data = json.loads(response.text)
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            code = err.get("code")
            if code is not None:
                return str(code)
    except (json.JSONDecodeError, ValueError):
        pass
    return None


async def post_chat(
    url: str,
    payload: dict,
    headers: dict[str, str],
    timeout: httpx.Timeout,
) -> dict:
    """POST a chat/completions payload, retrying transient errors.

    Backoff: 2^attempt seconds (2s, 4s), max 3 attempts. Deterministic
    balance/quota failures (BigModel 1113) raise
    :class:`LLMInsufficientBalanceError` immediately — retrying a broke
    account only wastes time.
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
            err_code = _parse_error_code(e.response)
            # Deterministic balance/quota exhaustion — fail loud, no retry.
            if err_code in _INSUFFICIENT_BALANCE_CODES:
                logger.error(
                    "[llm] HTTP %d error_code=%s from %s: %s",
                    code, err_code, url, e.response.text[:300],
                )
                raise LLMInsufficientBalanceError(
                    f"LLM 账户余额/额度不足（code {err_code}）: {e.response.text[:200]}。"
                    "请充值或更换 api_key。",
                    request=e.request, response=e.response,
                ) from e
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

