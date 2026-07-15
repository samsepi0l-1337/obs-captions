from __future__ import annotations

import json

import pytest

from obs_captions.stt.base import Transcript
from obs_captions.stt.openai_realtime import (
    _COMPLETED_EVENT,
    _DELTA_EVENT,
    _OUTPUT_TRANSCRIPT_DELTA,
    _OUTPUT_TRANSCRIPT_DONE,
    _TRANSLATE_OUTPUT_DELTA,
    OpenAIRealtimeBackend,
)
from tests._fake_ws import FakeWS, fake_connect, wait_for

_FAKE_KEY = "sk-test-openai"


def _make(
    ws: FakeWS,
    partials,
    finals,
    *,
    model: str = "gpt-realtime-whisper",
    delay: str | None = None,
    target_language: str | None = None,
    language: str = "ko",
):
    connect_fn, captured = fake_connect(ws)
    kwargs: dict[str, object] = {
        "api_key": _FAKE_KEY,
        "connect_fn": connect_fn,
        "language": language,
        "on_partial": partials.append,
        "on_final": finals.append,
        "model": model,
    }
    if delay is not None:
        kwargs["delay"] = delay
    if target_language is not None:
        kwargs["target_language"] = target_language
    backend = OpenAIRealtimeBackend(**kwargs)
    return backend, captured


def _session_update(ws: FakeWS) -> dict:
    updates = [m for m in ws.sent if '"session.update"' in m or '"transcription_session.update"' in m]
    assert updates, "expected a session.update message"
    return json.loads(updates[0])


@pytest.mark.asyncio
async def test_whisper_ga_connect_and_session_update():
    ws = FakeWS()
    backend, captured = _make(ws, [], [], model="gpt-realtime-whisper", delay="low")
    await backend.start_stream()
    try:
        assert captured["url"] == "wss://api.openai.com/v1/realtime"
        assert captured["headers"]["Authorization"] == f"Bearer {_FAKE_KEY}"
        assert "OpenAI-Beta" not in captured["headers"]
        payload = _session_update(ws)
        assert payload["type"] == "session.update"
        session = payload["session"]
        assert session["type"] == "transcription"
        transcription = session["audio"]["input"]["transcription"]
        assert transcription["model"] == "gpt-realtime-whisper"
        assert transcription["language"] == "ko"
        assert transcription["delay"] == "low"
        assert session["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_whisper_flush_commits_audio_buffer():
    ws = FakeWS()
    backend, _ = _make(ws, [], [], model="gpt-realtime-whisper")
    await backend.start_stream()
    try:
        await backend.flush()
        commits = [m for m in ws.sent if '"input_audio_buffer.commit"' in m]
        assert commits, "whisper mode must commit the input buffer on flush"
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_translate_connect_session_and_audio_append():
    ws = FakeWS()
    backend, captured = _make(
        ws, [], [], model="gpt-realtime-translate", target_language="en", language="ko"
    )
    await backend.start_stream()
    try:
        assert (
            captured["url"]
            == "wss://api.openai.com/v1/realtime/translations?model=gpt-realtime-translate"
        )
        assert "OpenAI-Beta" not in captured["headers"]
        payload = _session_update(ws)
        assert payload["type"] == "session.update"
        assert payload["session"]["audio"]["output"]["language"] == "en"
        await backend.feed_audio(b"\x00\x01" * 160)
        append = [m for m in ws.sent if '"session.input_audio_buffer.append"' in m]
        assert append, "translate mode uses session.input_audio_buffer.append"
        body = json.loads(append[0])
        assert isinstance(body["audio"], str) and body["audio"]
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_translate_output_transcript_delta_is_partial():
    partials: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, partials, [], model="gpt-realtime-translate", target_language="en")
    await backend.start_stream()
    try:
        ws.push(json.dumps({"type": _TRANSLATE_OUTPUT_DELTA, "delta": "Hello"}))
        ws.push(json.dumps({"type": _TRANSLATE_OUTPUT_DELTA, "delta": " world"}))
        await wait_for(lambda: len(partials) >= 2)
        assert partials[-1].text == "Hello world"
        assert partials[-1].is_final is False
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_translate_stop_sends_session_close():
    ws = FakeWS()
    backend, _ = _make(ws, [], [], model="gpt-realtime-translate", target_language="en")
    await backend.start_stream()
    await backend.stop_stream()
    closes = [m for m in ws.sent if '"session.close"' in m]
    assert closes, "translate mode must send session.close before teardown"


@pytest.mark.asyncio
async def test_realtime_21_connect_and_session_update():
    ws = FakeWS()
    backend, captured = _make(ws, [], [], model="gpt-realtime-2.1", language="ko")
    await backend.start_stream()
    try:
        assert captured["url"] == "wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1"
        assert "OpenAI-Beta" not in captured["headers"]
        payload = _session_update(ws)
        assert payload["type"] == "session.update"
        session = payload["session"]
        assert session["type"] == "realtime"
        assert session["model"] == "gpt-realtime-2.1"
        assert session["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24000}
        transcription = session["audio"]["input"]["transcription"]
        assert transcription["model"] == "gpt-realtime-whisper"
        assert transcription["language"] == "ko"
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_realtime_21_output_transcript_events():
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, partials, finals, model="gpt-realtime-2.1")
    await backend.start_stream()
    try:
        ws.push(json.dumps({"type": _OUTPUT_TRANSCRIPT_DELTA, "delta": "Hi"}))
        await wait_for(lambda: len(partials) >= 1)
        assert partials[-1].text == "Hi"
        ws.push(json.dumps({"type": _OUTPUT_TRANSCRIPT_DONE, "transcript": "Hi there"}))
        await wait_for(lambda: len(finals) >= 1)
        assert finals[-1].text == "Hi there"
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_feed_audio_appends_base64_pcm_for_whisper():
    ws = FakeWS()
    backend, _ = _make(ws, [], [], model="gpt-realtime-whisper")
    await backend.start_stream()
    try:
        await backend.feed_audio(b"\x00\x01" * 160)
        append = [m for m in ws.sent if '"input_audio_buffer.append"' in m]
        assert append, "expected an input_audio_buffer.append message"
        payload = json.loads(append[0])
        assert payload["type"] == "input_audio_buffer.append"
        assert isinstance(payload["audio"], str) and payload["audio"]
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_partial_delta_accumulates():
    partials: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, partials, [])
    await backend.start_stream()
    try:
        ws.push(json.dumps({"type": _DELTA_EVENT, "delta": "안녕"}))
        ws.push(json.dumps({"type": _DELTA_EVENT, "delta": "하세요"}))
        await wait_for(lambda: len(partials) >= 2)
        assert partials[-1].text == "안녕하세요"
        assert partials[-1].is_final is False
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_completed_event_emits_final():
    finals: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, [], finals)
    await backend.start_stream()
    try:
        ws.push(json.dumps({"type": _COMPLETED_EVENT, "transcript": "최종 전사"}))
        await wait_for(lambda: len(finals) >= 1)
        assert finals[-1].text == "최종 전사"
        assert finals[-1].is_final is True
    finally:
        await backend.stop_stream()


def test_missing_key_raises():
    import os

    old = os.environ.pop("OPENAI_API_KEY", None)
    try:
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            OpenAIRealtimeBackend(on_partial=lambda t: None, on_final=lambda t: None)
    finally:
        if old is not None:
            os.environ["OPENAI_API_KEY"] = old


def test_unknown_model_raises():
    with pytest.raises(ValueError, match="Unsupported OpenAI realtime model"):
        OpenAIRealtimeBackend(
            api_key=_FAKE_KEY,
            model="gpt-4o-transcribe",
            on_partial=lambda t: None,
            on_final=lambda t: None,
        )
