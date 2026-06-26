from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlparse

import pytest

from obs_captions.stt.assemblyai import AssemblyAIRealtimeBackend
from obs_captions.stt.base import Transcript
from tests._fake_ws import FakeWS, fake_connect, wait_for

_FAKE_KEY = "test-assemblyai-key"


def _make(ws: FakeWS, partials, finals, **kwargs):
    connect_fn, captured = fake_connect(ws)
    backend = AssemblyAIRealtimeBackend(
        api_key=_FAKE_KEY,
        connect_fn=connect_fn,
        on_partial=partials.append,
        on_final=finals.append,
        **kwargs,
    )
    return backend, captured


@pytest.mark.asyncio
async def test_connect_url_sample_rate_and_encoding():
    """WS URL must carry sample_rate and pcm_s16le encoding as query params."""
    ws = FakeWS()
    backend, captured = _make(ws, [], [])
    await backend.start_stream()
    try:
        parsed = urlparse(captured["url"])
        assert parsed.scheme == "wss"
        assert parsed.hostname == "streaming.assemblyai.com"
        assert parsed.path == "/v3/ws"
        qs = parse_qs(parsed.query)
        assert qs["sample_rate"] == ["16000"]
        assert qs["encoding"] == ["pcm_s16le"]
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_connect_auth_header_carries_api_key():
    """Authorization header must be the raw API key (no Bearer prefix)."""
    ws = FakeWS()
    backend, captured = _make(ws, [], [])
    await backend.start_stream()
    try:
        assert captured["headers"]["Authorization"] == _FAKE_KEY
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_feed_audio_sends_raw_pcm16_bytes():
    """Audio must be forwarded as raw PCM16 binary (not base64-wrapped)."""
    ws = FakeWS()
    backend, _ = _make(ws, [], [])
    await backend.start_stream()
    try:
        pcm = b"\x10\x20" * 80
        await backend.feed_audio(pcm)
        assert pcm in ws.sent
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_partial_turn_emits_on_partial():
    """Turn with end_of_turn=False must call on_partial with the full transcript."""
    partials: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, partials, [])
    await backend.start_stream()
    try:
        ws.push(json.dumps({"type": "Turn", "transcript": "안녕", "end_of_turn": False}))
        ws.push(json.dumps({"type": "Turn", "transcript": "안녕하세요", "end_of_turn": False}))
        await wait_for(lambda: len(partials) >= 2)
        assert partials[-1].text == "안녕하세요"
        assert partials[-1].is_final is False
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_end_of_turn_emits_on_final():
    """Turn with end_of_turn=True must call on_final with the committed transcript."""
    finals: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, [], finals)
    await backend.start_stream()
    try:
        ws.push(json.dumps({"type": "Turn", "transcript": "hello world", "end_of_turn": True}))
        await wait_for(lambda: len(finals) >= 1)
        assert finals[-1].text == "hello world"
        assert finals[-1].is_final is True
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_begin_and_termination_messages_are_ignored():
    """Begin and Termination server messages must not trigger any callbacks."""
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, partials, finals)
    await backend.start_stream()
    try:
        ws.push(json.dumps({"type": "Begin", "id": "sess_abc"}))
        ws.push(json.dumps({"type": "Termination", "audio_duration_seconds": 2.0}))
        await asyncio.sleep(0.05)
        assert len(partials) == 0
        assert len(finals) == 0
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_empty_transcript_end_of_turn_ignored():
    """An end_of_turn Turn with empty transcript must not call on_final."""
    finals: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, [], finals)
    await backend.start_stream()
    try:
        ws.push(json.dumps({"type": "Turn", "transcript": "", "end_of_turn": True}))
        await asyncio.sleep(0.05)
        assert len(finals) == 0
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_reconnect_on_disconnect():
    """Backend must reconnect when the WS connection drops while running."""
    finals: list[Transcript] = []
    ws1 = FakeWS()
    ws2 = FakeWS()
    call_count = 0

    async def _multi_connect(url: str, headers: dict[str, str]):
        nonlocal call_count
        call_count += 1
        return ws1 if call_count == 1 else ws2

    backend = AssemblyAIRealtimeBackend(
        api_key=_FAKE_KEY,
        connect_fn=_multi_connect,
        on_partial=lambda t: None,
        on_final=finals.append,
        sleep_fn=lambda _: asyncio.sleep(0),
    )
    await backend.start_stream()
    try:
        # Simulate connection drop by raising in recv on ws1.
        async def _fail():
            raise OSError("disconnected")

        ws1.recv = _fail  # type: ignore[method-assign]
        await wait_for(lambda: call_count >= 2)
        ws2.push(json.dumps({"type": "Turn", "transcript": "reconnected", "end_of_turn": True}))
        await wait_for(lambda: len(finals) >= 1)
        assert finals[-1].text == "reconnected"
    finally:
        await backend.stop_stream()


def test_missing_key_raises():
    """Missing API key must raise ValueError mentioning ASSEMBLYAI_API_KEY."""
    import os

    old = os.environ.pop("ASSEMBLYAI_API_KEY", None)
    try:
        with pytest.raises(ValueError, match="ASSEMBLYAI_API_KEY"):
            AssemblyAIRealtimeBackend(on_partial=lambda t: None, on_final=lambda t: None)
    finally:
        if old is not None:
            os.environ["ASSEMBLYAI_API_KEY"] = old
