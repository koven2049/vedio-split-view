"""describe_error must never yield a blank message.

WHY: a blank Task.error_message renders in the UI as a generic "network error",
which is what hid a real LLM ReadTimeout behind a misleading 小宇宙/网络 story.
"""
import httpx
import pytest

from video_split.service.error_text import describe_error


def _req() -> httpx.Request:
    return httpx.Request("POST", "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions")


@pytest.mark.parametrize("exc", [
    httpx.ReadTimeout("", request=_req()),
    httpx.ConnectTimeout("", request=_req()),
    httpx.ConnectError("", request=_req()),
    httpx.RemoteProtocolError("", request=_req()),
])
def test_blank_httpx_errors_get_readable_text(exc):
    assert str(exc) == ""
    msg = describe_error(exc)
    assert msg.strip()
    assert "open.bigmodel.cn" in msg


def test_read_timeout_points_at_the_timeout_knob():
    msg = describe_error(httpx.ReadTimeout("", request=_req()))
    assert "llm.timeout_ms" in msg


def test_http_status_error_includes_status_and_body():
    resp = httpx.Response(429, text='{"error":{"code":"1308","message":"已达到 5 小时的使用上限。"}}', request=_req())
    msg = describe_error(httpx.HTTPStatusError("", request=_req(), response=resp))
    assert "429" in msg
    assert "5 小时的使用上限" in msg


def test_plain_exception_message_preserved():
    assert describe_error(RuntimeError("boom")) == "boom"


def test_exception_with_no_message_falls_back_to_type_name():
    assert describe_error(RuntimeError()) == "RuntimeError"
