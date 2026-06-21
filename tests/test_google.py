from __future__ import annotations

import base64
import json

import pytest

from obs_captions.stt.base import Transcript
from obs_captions.stt.google import GoogleBackend
from tests._fake_ws import FakeWS, fake_connect, wait_for

_FAKE_KEY = "gemini-test-key"


def _make(ws: FakeWS, partials, finals):
    connect_fn, captured = fake_connect(ws)
    backend = GoogleBackend(
        api_key=_FAKE_KEY,
        connect_fn=connect_fn,
        language="ko",
        on_partial=partials.append,
        on_final=finals.append,
    )
    return backend, captured


@pytest.mark.asyncio
async def test_connect_url_carries_api_key_and_sends_setup():
    ws = FakeWS()
    backend, captured = _make(ws, [], [])
    await backend.start_stream()
    try:
        url = captured["url"]
        assert url.startswith(
            "wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key="
        )
        assert url.endswith(f"key={_FAKE_KEY}")
        setup = json.loads(ws.sent[0])
        assert setup["setup"]["model"] == "models/gemini-3.1-flash-live-preview"
        assert "inputAudioTranscription" in setup["setup"]
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_feed_audio_sends_realtime_input_pcm():
    ws = FakeWS()
    backend, _ = _make(ws, [], [])
    await backend.start_stream()
    try:
        pcm = b"\xaa\xbb" * 100
        await backend.feed_audio(pcm)
        msgs = [m for m in ws.sent if '"realtimeInput"' in m]
        assert msgs, "expected a realtimeInput message"
        payload = json.loads(msgs[0])
        audio = payload["realtimeInput"]["audio"]
        assert audio["mimeType"] == "audio/pcm;rate=16000"
        assert base64.b64decode(audio["data"]) == pcm
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_input_transcription_accumulates_partial():
    partials: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, partials, [])
    await backend.start_stream()
    try:
        ws.push(json.dumps({"serverContent": {"inputTranscription": {"text": "안녕"}}}))
        ws.push(json.dumps({"serverContent": {"inputTranscription": {"text": "하세요"}}}))
        await wait_for(lambda: len(partials) >= 2)
        assert partials[-1].text == "안녕하세요"
        assert partials[-1].is_final is False
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_turn_complete_emits_final_with_accumulated_text():
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, partials, finals)
    await backend.start_stream()
    try:
        ws.push(json.dumps({"serverContent": {"inputTranscription": {"text": "완성된 문장"}}}))
        await wait_for(lambda: len(partials) >= 1)
        ws.push(json.dumps({"serverContent": {"turnComplete": True}}))
        await wait_for(lambda: len(finals) >= 1)
        assert finals[-1].text == "완성된 문장"
        assert finals[-1].is_final is True
    finally:
        await backend.stop_stream()


def test_speech_v2_mode_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="speech_v2"):
        GoogleBackend(
            mode="speech_v2",
            on_partial=lambda t: None,
            on_final=lambda t: None,
        )


def test_unknown_mode_raises():
    with pytest.raises(ValueError, match="Unknown google mode"):
        GoogleBackend(
            mode="bogus",
            on_partial=lambda t: None,
            on_final=lambda t: None,
        )


def test_missing_key_raises():
    import os

    old = os.environ.pop("GEMINI_API_KEY", None)
    try:
        with pytest.raises(ValueError, match="GEMINI_API_KEY"):
            GoogleBackend(on_partial=lambda t: None, on_final=lambda t: None)
    finally:
        if old is not None:
            os.environ["GEMINI_API_KEY"] = old
