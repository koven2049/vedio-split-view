"""Human-readable messages for exceptions surfaced to the frontend.

Motivation: ``str(httpx.ReadTimeout())`` and friends are the **empty string** —
httpx timeout/connect errors carry no message. Writing that straight into
``Task.error_message`` gave the UI a blank error, which it rendered as a
generic "network error", hiding the real cause (LLM timeout, rate limit).

Every user-facing error path goes through :func:`describe_error` so a blank
exception can never reach the UI.
"""
from __future__ import annotations

import httpx

_HTTPX_HINTS: tuple[tuple[type[Exception], str], ...] = (
    (httpx.ConnectTimeout, "连接上游服务超时（{where}），请检查网络或稍后重试"),
    (httpx.ReadTimeout, "等待上游服务响应超时（{where}）。长音频请调大 llm.timeout_ms 后重试"),
    (httpx.WriteTimeout, "向上游服务发送数据超时（{where}）"),
    (httpx.PoolTimeout, "连接池等待超时（{where}）"),
    (httpx.ConnectError, "无法连接上游服务（{where}）"),
    (httpx.RemoteProtocolError, "上游服务提前断开连接（{where}）"),
)


def _request_target(exc: Exception) -> str:
    request = getattr(exc, "request", None)
    if request is None:
        return "未知地址"
    url = request.url
    return f"{url.scheme}://{url.host}{url.path}"


def describe_error(exc: Exception) -> str:
    """Return a non-empty, user-readable description of ``exc``.

    Falls back to ``ExceptionType: str(exc)`` so an unmapped exception is still
    identifiable, and to the bare type name when ``str(exc)`` is empty.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text[:200].strip()
        return (
            f"上游服务返回 HTTP {exc.response.status_code}"
            f"（{_request_target(exc)}）{': ' + body if body else ''}"
        )

    for exc_type, template in _HTTPX_HINTS:
        if isinstance(exc, exc_type):
            return template.format(where=_request_target(exc))

    text = str(exc).strip()
    if text:
        return text
    return type(exc).__name__
