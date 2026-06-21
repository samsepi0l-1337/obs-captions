from __future__ import annotations

import base64
import json

import pytest

from obs_captions.stt.base import Transcript
from obs_captions.stt.elevenlabs_realtime import ElevenLabsRealtimeBackend
from tests._fake_ws import FakeWS, fake_connect, wait_for

_FAKE_KEY = "xi-test-key"


def _make(ws: FakeWS, partials, finals):
    connect_fn, captured = fake_connect(ws)
    backend = ElevenLabsRealtimeBackend(
        api_key=_FAKE_KEY,
        connect_fn=connect_fn,
        language="ko",
        on_partial=partials.append,
        on_final=finals.append,
    )
    return backend, captured


@pytest.mark.asyncio
async def test_connect_url_and_xi_api_key():
    ws = FakeWS()
    backend, captured = _make(ws, [], [])
    await backend.start_stream()
    try:
        assert captured["url"] == "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
        assert captured["headers"]["xi-api-key"] == _FAKE_KEY
        # First frame is the session config with the model id.
        config = json.loads(ws.sent[0])
        assert config["model_id"] == "scribe_v2_realtime"
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_feed_audio_sends_input_audio_chunk_base64():
    ws = FakeWS()
    backend, _ = _make(ws, [], [])
    await backend.start_stream()
    try:
        pcm = b"\x10\x20" * 80
        await backend.feed_audio(pcm)
        chunks = [m for m in ws.sent if '"input_audio_chunk"' in m]
        assert chunks, "expected an input_audio_chunk message"
        payload = json.loads(chunks[0])
        assert payload["type"] == "input_audio_chunk"
        assert base64.b64decode(payload["audio_chunk"]) == pcm
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_partial_transcript_emits_full_hypothesis():
    partials: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, partials, [])
    await backend.start_stream()
    try:
        ws.push(json.dumps({"type": "partial_transcript", "text": "안녕"}))
        ws.push(json.dumps({"type": "partial_transcript", "text": "안녕하세요"}))
        await wait_for(lambda: len(partials) >= 2)
        assert partials[-1].text == "안녕하세요"
        assert partials[-1].is_final is False
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_committed_transcript_emits_final():
    finals: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, [], finals)
    await backend.start_stream()
    try:
        ws.push(json.dumps({"type": "committed_transcript", "text": "확정 자막"}))
        await wait_for(lambda: len(finals) >= 1)
        assert finals[-1].text == "확정 자막"
        assert finals[-1].is_final is True
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_committed_transcript_nested_text():
    finals: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, [], finals)
    await backend.start_stream()
    try:
        ws.push(json.dumps({"type": "committed_transcript", "transcript": {"text": "중첩"}}))
        await wait_for(lambda: len(finals) >= 1)
        assert finals[-1].text == "중첩"
    finally:
        await backend.stop_stream()


def test_missing_key_raises():
    import os

    old = os.environ.pop("ELEVENLABS_API_KEY", None)
    try:
        with pytest.raises(ValueError, match="ELEVENLABS_API_KEY"):
            ElevenLabsRealtimeBackend(on_partial=lambda t: None, on_final=lambda t: None)
    finally:
        if old is not None:
            os.environ["ELEVENLABS_API_KEY"] = old
