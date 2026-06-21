from __future__ import annotations

import json

import pytest

from obs_captions.stt.base import Transcript
from obs_captions.stt.xai import XaiBackend
from tests._fake_ws import FakeWS, fake_connect, wait_for

_FAKE_KEY = "xai-test-key"


def _make(ws: FakeWS, partials, finals):
    connect_fn, captured = fake_connect(ws)
    backend = XaiBackend(
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
        url = captured["url"]
        assert url.startswith("wss://api.x.ai/v1/stt?")
        assert "sample_rate=16000" in url
        assert "encoding=pcm" in url
        assert "language=ko" in url
        assert captured["headers"]["Authorization"] == f"Bearer {_FAKE_KEY}"
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_feed_audio_sends_raw_binary_frame():
    ws = FakeWS()
    backend, _ = _make(ws, [], [])
    await backend.start_stream()
    try:
        pcm = b"\x01\x02\x03\x04"
        await backend.feed_audio(pcm)
        # No setup frame for xAI; the only sent message is the raw audio.
        assert ws.sent == [pcm]
        assert isinstance(ws.sent[0], bytes)
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_partial_transcript_event():
    partials: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, partials, [])
    await backend.start_stream()
    try:
        ws.push(json.dumps({"type": "transcript.created"}))
        ws.push(json.dumps({"type": "transcript.partial", "text": "안녕하세요", "is_final": False}))
        await wait_for(lambda: len(partials) >= 1)
        assert partials[-1].text == "안녕하세요"
        assert partials[-1].is_final is False
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_transcript_done_emits_final():
    finals: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, [], finals)
    await backend.start_stream()
    try:
        ws.push(json.dumps({"type": "transcript.done", "text": "최종 결과", "duration": 1.2}))
        await wait_for(lambda: len(finals) >= 1)
        assert finals[-1].text == "최종 결과"
        assert finals[-1].is_final is True
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_speech_final_partial_commits():
    finals: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, [], finals)
    await backend.start_stream()
    try:
        ws.push(
            json.dumps(
                {
                    "type": "transcript.partial",
                    "text": "발화 종료",
                    "is_final": True,
                    "speech_final": True,
                }
            )
        )
        await wait_for(lambda: len(finals) >= 1)
        assert finals[-1].text == "발화 종료"
    finally:
        await backend.stop_stream()


def test_missing_key_raises():
    import os

    old = os.environ.pop("XAI_API_KEY", None)
    try:
        with pytest.raises(ValueError, match="XAI_API_KEY"):
            XaiBackend(on_partial=lambda t: None, on_final=lambda t: None)
    finally:
        if old is not None:
            os.environ["XAI_API_KEY"] = old
