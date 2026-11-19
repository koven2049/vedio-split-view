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
    assert transcriber._model_supports_diarization("qwen-audio-3.1-asr-flash-filetrans")
    # qwen3 filetrans has no diarization parameter at all — transcripts carry
    # no speaker_id, dialogue formatting falls back to LLM speaker guessing.
    assert not transcriber._model_supports_diarization("qwen3-asr-flash-filetrans")
    assert not transcriber._model_supports_diarization("whisper-1")


# ─── submit body: qwen3 dialect vs classic dialect ───


def test_build_submit_body_qwen3_dialect():
    body = transcriber._build_funasr_submit_body(
        "qwen3-asr-flash-filetrans", "https://oss/chunk.mp3", ""
    )
    # singular file_url, no diarization param, no parameters key when no lang
    assert body == {
        "model": "qwen3-asr-flash-filetrans",
        "input": {"file_url": "https://oss/chunk.mp3"},
    }
    body = transcriber._build_funasr_submit_body(
        "qwen3-asr-flash-filetrans", "https://oss/chunk.mp3", "zh"
    )
    assert body["parameters"] == {"language": "zh"}
    assert "diarization_enabled" not in body.get("parameters", {})


def test_build_submit_body_classic_dialect():
    body = transcriber._build_funasr_submit_body(
        "qwen-audio-3.0-asr-flash-filetrans", "https://oss/chunk.mp3", "zh"
    )
    assert body["input"] == {"file_urls": ["https://oss/chunk.mp3"]}
    assert body["parameters"] == {"language_hints": ["zh"], "diarization_enabled": True}
    body = transcriber._build_funasr_submit_body("fun-asr", "https://oss/c.mp3", "")
    assert body["parameters"] == {"diarization_enabled": True}
    # qwen-audio-3.1 filetrans keeps the classic spelling (verified live);
    # speaker_diarization_enabled belongs to the synchronous 3.1 model only.
    body = transcriber._build_funasr_submit_body(
        "qwen-audio-3.1-asr-flash-filetrans", "https://oss/c.mp3", ""
    )
    assert body == {
        "model": "qwen-audio-3.1-asr-flash-filetrans",
        "input": {"file_urls": ["https://oss/c.mp3"]},
        "parameters": {"diarization_enabled": True},
    }


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


@pytest.mark.asyncio
async def test_parse_funasr_results_keeps_integer_speaker_zero():
    """DashScope returns speaker_id as an int starting at 0 (live qwen-audio-3.1
    result). `0 or ""` used to drop speaker 0 to unlabeled, pushing a 3-host
    episode under the 80% label threshold and back to LLM guessing."""
    output = {"results": [{"subtask_status": "SUCCEEDED", "transcription_url": "u"}]}
    tr_payload = {
        "transcripts": [
            {
                "sentences": [
                    {"begin_time": 0, "end_time": 1000, "text": "甲", "speaker_id": 0},
                    {"begin_time": 1000, "end_time": 2000, "text": "乙", "speaker_id": 1},
                    {"begin_time": 2000, "end_time": 3000, "text": "丙"},
                ]
            }
        ]
    }
    entries = await transcriber._parse_funasr_results(_FakeClient(tr_payload), output)
    assert [e.speaker for e in entries] == ["0", "1", ""]


@pytest.mark.asyncio
async def test_parse_funasr_results_qwen3_single_result_shape():
    """qwen3 filetrans: one output.result (singular), sentences without speaker_id."""
    output = {
        "result": {"transcription_url": "https://example.com/tr.json"},
    }
    tr_payload = {
        "transcripts": [
            {
                "sentences": [
                    {"begin_time": 0, "end_time": 2000, "text": "你好"},
                    {"begin_time": 2100, "end_time": 4000, "text": "第二句"},
                ]
            }
        ]
    }
    entries = await transcriber._parse_funasr_results(_FakeClient(tr_payload), output)
    assert [(e.text, e.speaker) for e in entries] == [("你好", ""), ("第二句", "")]


@pytest.mark.asyncio
async def test_parse_funasr_results_skips_failed_subtask():
    output = {
        "results": [
            {"subtask_status": "FAILED", "transcription_url": "https://example.com/x.json"},
            {"subtask_status": "SUCCEEDED", "transcription_url": "https://example.com/tr.json"},
        ]
    }
    tr_payload = {
        "transcripts": [{"sentences": [{"begin_time": 0, "end_time": 1000, "text": "ok"}]}]
    }
    entries = await transcriber._parse_funasr_results(_FakeClient(tr_payload), output)
    assert [(e.text, e.speaker) for e in entries] == [("ok", "")]


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


def _fake_settings(monkeypatch, model: str, chunk: int = 300):
    class _Transcription:
        chunk_duration_seconds = chunk

    _Transcription.model = model

    class _Settings:
        transcription = _Transcription()

    monkeypatch.setattr(transcriber, "get_settings", lambda: _Settings())


def test_poll_limit_covers_whole_episode_task(monkeypatch):
    """One diarization task holds up to 2h of audio (73 min took 145s live);
    a fixed 300s poll budget would time it out under load."""
    _fake_settings(monkeypatch, "qwen-audio-3.1-asr-flash-filetrans")
    assert transcriber._poll_limit_seconds() >= transcriber.DIARIZATION_CHUNK_SECONDS
    _fake_settings(monkeypatch, "qwen3-asr-flash-filetrans")
    assert transcriber._poll_limit_seconds() == transcriber._FUNASR_MAX_POLL


def test_chunk_seconds_diarization_uses_whole_episode(monkeypatch):
    _fake_settings(monkeypatch, "qwen-audio-3.1-asr-flash-filetrans")
    assert transcriber._chunk_seconds() == transcriber.DIARIZATION_CHUNK_SECONDS
    _fake_settings(monkeypatch, "qwen3-asr-flash-filetrans")
    assert transcriber._chunk_seconds() == 300


def test_split_short_audio_still_reencodes_mono_for_diarization(monkeypatch, tmp_path):
    """Audio shorter than one chunk must not be sent as-is (stereo AAC →
    no speaker_id): diarization models always get one mono mp3 chunk."""
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"x" * 2048)
    _fake_settings(monkeypatch, "qwen-audio-3.1-asr-flash-filetrans")
    monkeypatch.setattr(transcriber, "get_audio_duration", lambda p: 4415.0)
    monkeypatch.setattr(transcriber, "probe_audio_codec", lambda p: "aac")
    captured: dict = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        (audio.parent / "chunks" / "chunk_000.mp3").write_bytes(b"y" * 2048)
        return transcriber.subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(transcriber.subprocess, "run", _fake_run)
    chunks = transcriber.split_audio(audio)

    assert chunks != [audio]
    assert [c.name for c in chunks] == ["chunk_000.mp3"]
    cmd = captured["cmd"]
    assert cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[cmd.index("-segment_time") + 1] == str(transcriber.DIARIZATION_CHUNK_SECONDS)


def test_split_short_audio_unchanged_without_diarization(monkeypatch, tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"x" * 2048)
    _fake_settings(monkeypatch, "qwen3-asr-flash-filetrans")
    monkeypatch.setattr(transcriber, "get_audio_duration", lambda p: 200.0)
    assert transcriber.split_audio(audio) == [audio]


@pytest.mark.asyncio
async def test_speakers_namespaced_per_chunk(monkeypatch, tmp_path):
    """speaker_id restarts at 0 in every ASR task — chunk 1's "0" is not
    chunk 0's "0", so labels must stay distinct after merging."""
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"x")
    chunks = [tmp_path / "chunk_000.mp3", tmp_path / "chunk_001.mp3"]
    for c in chunks:
        c.write_bytes(b"y")
    _fake_settings(monkeypatch, "qwen-audio-3.1-asr-flash-filetrans")
    monkeypatch.setattr(transcriber, "split_audio", lambda p, **kw: chunks)

    async def _fake_chunk(path, **kwargs):
        return [
            SubtitleEntry(start=1.0, duration=1.0, text="a", speaker="0"),
            SubtitleEntry(start=2.0, duration=1.0, text="b", speaker="1"),
        ], 10.0

    monkeypatch.setattr(transcriber, "transcribe_single_chunk", _fake_chunk)
    entries, _ = await transcriber.transcribe_audio(audio)

    assert [e.speaker for e in entries] == ["0:0", "0:1", "1:0", "1:1"]
    # offsets follow the diarization chunk length, not config's 300s
    assert entries[2].start == pytest.approx(transcriber.DIARIZATION_CHUNK_SECONDS + 1.0)


@pytest.mark.asyncio
async def test_single_chunk_keeps_raw_speaker_ids(monkeypatch, tmp_path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"x")
    chunk = tmp_path / "chunk_000.mp3"
    chunk.write_bytes(b"y")
    _fake_settings(monkeypatch, "qwen-audio-3.1-asr-flash-filetrans")
    monkeypatch.setattr(transcriber, "split_audio", lambda p, **kw: [chunk])

    async def _fake_chunk(path, **kwargs):
        return [SubtitleEntry(start=1.0, duration=1.0, text="a", speaker="2")], 10.0

    monkeypatch.setattr(transcriber, "transcribe_single_chunk", _fake_chunk)
    entries, _ = await transcriber.transcribe_audio(audio)
    assert [e.speaker for e in entries] == ["2"]
    assert entries[0].start == pytest.approx(1.0)


def test_transcript_cache_keeps_speaker_and_checks_chunk_size(tmp_path):
    chunk = tmp_path / "chunk_000.mp3"
    chunk.write_bytes(b"y" * 100)
    entries = [SubtitleEntry(start=1.0, duration=2.0, text="hi", speaker="1")]
    transcriber._save_transcript_cache(chunk, entries)

    assert transcriber._load_cached_transcript(chunk) == entries
    # a re-split with another chunk length produces a different file
    chunk.write_bytes(b"y" * 5000)
    assert transcriber._load_cached_transcript(chunk) is None


def test_legacy_list_cache_is_ignored(tmp_path):
    chunk = tmp_path / "chunk_000.mp3"
    chunk.write_bytes(b"y")
    transcriber._transcript_cache_path(chunk).write_text('[{"start": 0, "duration": 1, "text": "x"}]')
    assert transcriber._load_cached_transcript(chunk) is None


@pytest.mark.asyncio
async def test_asr_labels_with_scattered_empty_lines_do_not_crash(monkeypatch, _config):
    """Regression (video 58/60): diarization labels covering >80% but with
    interleaved untagged lines raised KeyError('') and killed the dialogue."""

    async def _no_llm(*a, **k):
        raise AssertionError("LLM must not be called when ASR labels cover the transcript")

    monkeypatch.setattr(dialogue_mod, "post_chat", _no_llm)
    labels = ["0"] * 4 + [""] + ["1"] * 3 + [""] + ["0"] * 2  # 9/11 labeled
    subs = _subs_with_speakers(labels)
    result, usage = await format_dialogue(
        subs, platform="xiaoyuzhou", uploader="u", title="t", duration_seconds=60
    )
    assert result["fallback"] is False
    assert result["source"] == "asr"
    covered = [i for b in result["blocks"] for i in range(b["f"], b["t"] + 1)]
    assert covered == list(range(11))
    # leading empty line inherits the first label; runs split on real changes
    speakers = {b["s"] for b in result["blocks"]}
    assert speakers == {"S1", "S2"}
