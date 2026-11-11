"""Fun-ASR empty-result handling: SUCCEEDED with no audio is a failure.

Task #69: DashScope returned ``succeeded (0.0s), billed_duration=0.0s`` with
a hollow transcript; the pipeline treated it as success, kept translating,
and completed the task with an essentially empty note. These tests pin the
contract:

- SUCCEEDED + billed_duration<=0 (or no parsable text) → resubmit once;
- still empty → RuntimeError that fails the chunk and aborts the task —
  never a cached empty transcript, never "complete";
- a healthy second attempt recovers;
- a genuine no-speech FAILED stays tolerated (no resubmit) — silence in one
  chunk of a long recording must not kill the job.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from video_split.service import transcriber


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Replays a scripted sequence of poll / transcription payloads.

    One instance per ``httpx.AsyncClient(...)`` call: a retry submits a fresh
    task through a fresh client, so ``instances`` records how many ASR tasks
    were really submitted.
    """

    instances: ClassVar[list[_FakeAsyncClient]] = []

    def __init__(self, script: list[dict], **kwargs):
        self._script = list(script)
        self.submits = 0
        _FakeAsyncClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        self.submits += 1
        return _FakeResponse(
            {"output": {"task_id": f"task-{len(_FakeAsyncClient.instances)}-{self.submits}"}}
        )

    async def get(self, url, **kwargs):
        return _FakeResponse(self._script.pop(0))


def _poll_payload(duration: float) -> dict:
    return {
        "output": {
            "task_status": "SUCCEEDED",
            "results": [{"subtask_status": "SUCCEEDED", "transcription_url": "https://tr/x.json"}],
        },
        "usage": {"duration": duration},
    }


_TRANSCRIPT_WITH_TEXT = {
    "transcripts": [{"sentences": [{"begin_time": 0, "end_time": 3200, "text": "欢迎收听"}]}]
}


@pytest.fixture
def _settings(monkeypatch):
    class _Transcription:
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        model = "fun-asr"
        api_key = "test-key"
        language = "zh"

    class _Settings:
        transcription = _Transcription()

    monkeypatch.setattr(transcriber, "get_settings", lambda: _Settings())


@pytest.fixture
def _fast_poll(monkeypatch):
    monkeypatch.setattr(transcriber, "_FUNASR_POLL_INTERVAL", 0)


def _script_clients(monkeypatch, scripts: list[list[dict]]) -> None:
    _FakeAsyncClient.instances = []
    remaining = list(scripts)

    def _factory(**kwargs):
        return _FakeAsyncClient(remaining.pop(0), **kwargs)

    monkeypatch.setattr(transcriber.httpx, "AsyncClient", _factory)


@pytest.mark.asyncio
async def test_empty_result_retried_once_then_fails(monkeypatch, _settings, _fast_poll):
    """Task #69 shape: succeeded / billed 0.0s / hollow text on both attempts."""
    _script_clients(
        monkeypatch,
        [[_poll_payload(0.0), _TRANSCRIPT_WITH_TEXT]] * 2,
    )

    with pytest.raises(RuntimeError, match="空结果重试一次后仍为空"):
        await transcriber._funasr_call("https://oss/audio.mp3")

    submits = [c.submits for c in _FakeAsyncClient.instances]
    assert submits == [1, 1], "must resubmit exactly one fresh task before failing"


@pytest.mark.asyncio
async def test_empty_first_attempt_recovers_on_retry(monkeypatch, _settings, _fast_poll):
    _script_clients(
        monkeypatch,
        [
            [_poll_payload(0.0), _TRANSCRIPT_WITH_TEXT],  # sick first task
            [_poll_payload(120.0), _TRANSCRIPT_WITH_TEXT],  # healthy retry
        ],
    )

    entries, dur = await transcriber._funasr_call("https://oss/audio.mp3")

    assert dur == 120.0
    assert [e.text for e in entries] == ["欢迎收听"]
    assert len(_FakeAsyncClient.instances) == 2


@pytest.mark.asyncio
async def test_healthy_result_returns_without_retry(monkeypatch, _settings, _fast_poll):
    _script_clients(monkeypatch, [[_poll_payload(300.0), _TRANSCRIPT_WITH_TEXT]])

    entries, dur = await transcriber._funasr_call("https://oss/audio.mp3")

    assert dur == 300.0
    assert [e.text for e in entries] == ["欢迎收听"]
    assert len(_FakeAsyncClient.instances) == 1, "a good result must not resubmit"


@pytest.mark.asyncio
async def test_no_speech_failure_still_tolerated(monkeypatch, _settings, _fast_poll):
    """Silence in one chunk degrades to an empty transcript — no resubmit, no abort."""
    _script_clients(
        monkeypatch,
        [
            [
                {
                    "output": {
                        "task_status": "FAILED",
                        "code": "ASR_RESPONSE_HAVE_NO_WORDS",
                        "message": "audio has no words",
                    }
                }
            ]
        ],
    )

    entries, dur = await transcriber._funasr_call("https://oss/audio.mp3")

    assert entries == []
    assert dur == 0.0
    assert len(_FakeAsyncClient.instances) == 1
