from __future__ import annotations

import json

import pytest

from obs_captions.stt.base import Transcript
from obs_captions.stt.openai_realtime import _COMPLETED_EVENT, _DELTA_EVENT, OpenAIRealtimeBackend
from tests._fake_ws import FakeWS, fake_connect, wait_for

_FAKE_KEY = "sk-test-openai"


def _make(ws: FakeWS, partials, finals):
    connect_fn, captured = fake_connect(ws)
    backend = OpenAIRealtimeBackend(
        api_key=_FAKE_KEY,
        connect_fn=connect_fn,
        language="ko",
        on_partial=partials.append,
        on_final=finals.append,
    )
    return backend, captured


@pytest.mark.asyncio
async def test_connect_url_and_bearer_auth():
    ws = FakeWS()
    backend, captured = _make(ws, [], [])
    await backend.start_stream()
    try:
        assert captured["url"] == "wss://api.openai.com/v1/realtime?intent=transcription"
        assert captured["headers"]["Authorization"] == f"Bearer {_FAKE_KEY}"
        assert captured["headers"]["OpenAI-Beta"] == "realtime=v1"
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_feed_audio_appends_base64_pcm():
    ws = FakeWS()
    backend, _ = _make(ws, [], [])
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
