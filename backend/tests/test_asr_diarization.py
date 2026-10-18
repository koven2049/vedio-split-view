"""ASR diarization: model routing, sentence parsing, dialogue layout priority."""

from __future__ import annotations

import pytest

from video_split.service import dialogue as dialogue_mod
from video_split.service import transcriber
from video_split.service.dialogue import format_dialogue
from video_split.service.downloader import SubtitleEntry


@pytest.fixture
def _config(test_config_path):
    """Point get_settings at the generated test config (same as _llm_config)."""
    from video_split.config import set_config_path

    set_config_path(test_config_path)

# ─── model routing / submit params ───


def test_model_supports_diarization_prefixes():
    assert transcriber._model_supports_diarization("fun-asr")
    assert transcriber._model_supports_diarization("fun-asr-flash-2026-06-15")
    assert transcriber._model_supports_diarization("paraformer-v2")
    assert transcriber._model_supports_diarization("qwen-audio-3.0-asr-flash-filetrans")
    assert not transcriber._model_supports_diarization("qwen-audio-3.1-asr-flash-filetrans")
    assert not transcriber._model_supports_diarization("whisper-1")


def test_dashscope_routing_covers_qwen_filetrans(monkeypatch):
    class _Transcription:
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        model = "qwen-audio-3.0-asr-flash-filetrans"

    class _Settings:
        transcription = _Transcription()

    monkeypatch.setattr(transcriber, "get_settings", lambda: _Settings())
    assert transcriber._is_dashscope_funasr()

    _Transcription.model = "whisper-1"
    assert not transcriber._is_dashscope_funasr()


# ─── result parsing carries speaker_id ───


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    async def get(self, url):
        return _FakeResponse(self._payload)


@pytest.mark.asyncio
async def test_parse_funasr_results_captures_speaker_id():
    output = {
        "results": [
            {
                "subtask_status": "SUCCEEDED",
                "transcription_url": "https://example.com/tr.json",
            }
        ]
    }
    tr_payload = {
        "transcripts": [
            {
                "sentences": [
                    {"begin_time": 0, "end_time": 2000, "text": "你好 ", "speaker_id": "0"},
                    {"begin_time": 2100, "end_time": 4000, "text": "你好呀", "speaker_id": "1"},
                    {"begin_time": 4100, "end_time": 5000, "text": "", "speaker_id": "0"},
                ]
            }
        ]
    }
    entries = await transcriber._parse_funasr_results(_FakeClient(tr_payload), output)
    assert [(e.text, e.speaker) for e in entries] == [("你好", "0"), ("你好呀", "1")]


# ─── dialogue: ASR labels take priority over the LLM ───


def _subs_with_speakers(labels):
    return [
        SubtitleEntry(start=i * 6.0, duration=5.0, text=f"第{i}句话的内容。", speaker=lab)
        for i, lab in enumerate(labels)
    ]


@pytest.mark.asyncio
async def test_asr_labels_build_layout_without_llm(monkeypatch, _config):
    async def _no_llm(*a, **k):
        raise AssertionError("LLM must not be called when ASR labels cover the transcript")

    monkeypatch.setattr(dialogue_mod, "post_chat", _no_llm)
    subs = _subs_with_speakers(["0"] * 5 + ["1"] * 5)
    result, usage = await format_dialogue(
        subs, platform="xiaoyuzhou", uploader="u", title="t", duration_seconds=60
    )
    assert result["fallback"] is False
    assert result.get("source") == "asr"
    assert [s["name"] for s in result["speakers"]] == ["说话人1", "说话人2"]
    # one block per speaker run (each run is short here)
    assert [(b["s"], b["f"], b["t"]) for b in result["blocks"]] == [("S1", 0, 4), ("S2", 5, 9)]
    assert usage == {}


@pytest.mark.asyncio
async def test_asr_sparse_labels_fall_through_to_llm(monkeypatch, _config):
    payload = {
        "speakers": [{"id": "A", "name": "主持", "role": ""}],
        "blocks": [{"s": "A", "f": 0, "t": 9}],
        "sections": [],
    }
    calls: list[str] = []

    async def _fake_post_chat(url, payload_dict, headers, timeout):
        calls.append(url)
        return {
            "choices": [{"message": {"content": __import__("json").dumps(payload)}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr(dialogue_mod, "post_chat", _fake_post_chat)
    # labels on only 3/10 lines — too sparse, must take the LLM path
    subs = _subs_with_speakers(["0", "", "0", "", "", "", "", "", "", ""])
    result, _usage = await format_dialogue(
        subs, platform="youtube", uploader="u", title="t", duration_seconds=60
    )
    assert len(calls) == 1
    assert result["fallback"] is False
    assert "source" not in result


# ─── split_audio: diarization models get mono mp3 chunks ───


def test_split_reencodes_mono_mp3_for_diarization(monkeypatch, tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"x" * 2048)
    monkeypatch.setattr(transcriber, "get_audio_duration", lambda p: 700.0)
    monkeypatch.setattr(transcriber, "probe_audio_codec", lambda p: "aac")
    monkeypatch.setattr(transcriber, "_model_supports_diarization", lambda m: True)
    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        chunk_dir = audio.parent / "chunks"
        for i in range(3):
            (chunk_dir / f"chunk_{i:03d}.mp3").write_bytes(b"y" * 2048)
        return transcriber.subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(transcriber.subprocess, "run", _fake_run)
    chunks = transcriber.split_audio(audio, chunk_seconds=300)

    cmd = captured["cmd"]
    assert cmd[cmd.index("-ac") + 1] == "1", "diarization chunks must be mono"
    assert "libmp3lame" in cmd, "stream copy yields no speaker_id — must re-encode"
    assert all(c.suffix == ".mp3" for c in chunks)
