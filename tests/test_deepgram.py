from __future__ import annotations

import asyncio
import json

import pytest

from obs_captions.stt.base import Transcript
from obs_captions.stt.deepgram import DeepgramBackend
from tests._fake_ws import FakeWS, fake_connect, wait_for

_FAKE_KEY = "deepgram-test-key"


def _make(ws: FakeWS, partials, finals):
    connect_fn, captured = fake_connect(ws)
    backend = DeepgramBackend(
        api_key=_FAKE_KEY,
        connect_fn=connect_fn,
        language="ko",
        on_partial=partials.append,
        on_final=finals.append,
    )
    return backend, captured


@pytest.mark.asyncio
async def test_connect_url_and_token_auth():
    ws = FakeWS()
    backend, captured = _make(ws, [], [])
    await backend.start_stream()
    try:
        url = captured["url"]
        assert url.startswith("wss://api.deepgram.com/v1/listen?")
        assert "encoding=linear16" in url
        assert "sample_rate=16000" in url
        assert "interim_results=true" in url
        assert "language=ko" in url
        # Deepgram uses "Token" scheme, not "Bearer"
        assert captured["headers"]["Authorization"] == f"Token {_FAKE_KEY}"
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
        # No setup frame for Deepgram; the only sent message is the raw audio.
        assert ws.sent == [pcm]
        assert isinstance(ws.sent[0], bytes)
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_interim_result_emits_partial():
    partials: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, partials, [])
    await backend.start_stream()
    try:
        ws.push(
            json.dumps(
                {
                    "type": "Results",
                    "channel": {"alternatives": [{"transcript": "안녕하세요"}]},
                    "is_final": False,
                    "speech_final": False,
                }
            )
        )
        await wait_for(lambda: len(partials) >= 1)
        assert partials[-1].text == "안녕하세요"
        assert partials[-1].is_final is False
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_final_result_emits_final():
    finals: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, [], finals)
    await backend.start_stream()
    try:
        ws.push(
            json.dumps(
                {
                    "type": "Results",
                    "channel": {"alternatives": [{"transcript": "최종 결과"}]},
                    "is_final": True,
                    "speech_final": False,
                }
            )
        )
        await wait_for(lambda: len(finals) >= 1)
        assert finals[-1].text == "최종 결과"
        assert finals[-1].is_final is True
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_speech_final_emits_final():
    finals: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, [], finals)
    await backend.start_stream()
    try:
        ws.push(
            json.dumps(
                {
                    "type": "Results",
                    "channel": {"alternatives": [{"transcript": "발화 종료"}]},
                    "is_final": True,
                    "speech_final": True,
                }
            )
        )
        await wait_for(lambda: len(finals) >= 1)
        assert finals[-1].text == "발화 종료"
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_empty_transcript_ignored():
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, partials, finals)
    await backend.start_stream()
    try:
        ws.push(
            json.dumps(
                {
                    "type": "Results",
                    "channel": {"alternatives": [{"transcript": ""}]},
                    "is_final": True,
                    "speech_final": False,
                }
            )
        )
        await asyncio.sleep(0.05)
        assert len(partials) == 0
        assert len(finals) == 0
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_non_results_message_ignored():
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, partials, finals)
    await backend.start_stream()
    try:
        # Deepgram sends a Metadata message on connect — should be silently ignored
        ws.push(json.dumps({"type": "Metadata", "transaction_key": "abc"}))
        await asyncio.sleep(0.05)
        assert len(partials) == 0
        assert len(finals) == 0
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_reconnect_uses_base_backoff(monkeypatch):
    """Base-class reconnect logic fires on connection drop (no network needed)."""
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    ws = FakeWS()
    connect_fn, _ = fake_connect(ws)
    backend = DeepgramBackend(
        api_key=_FAKE_KEY,
        connect_fn=connect_fn,
        language="ko",
        on_partial=lambda t: None,
        on_final=lambda t: None,
        max_reconnects=1,
        backoff_base=0.01,
        sleep_fn=fake_sleep,
    )
    await backend.start_stream()
    # Force a connection error by closing the WS and pushing nothing
    ws.closed = True
    # Simulate recv raising to trigger reconnect path
    async def _raise():
        raise OSError("dropped")

    ws.recv = _raise  # type: ignore[method-assign]
    await asyncio.sleep(0.05)
    await backend.stop_stream()
    assert slept, "backoff sleep should have been called on reconnect"


def test_missing_key_raises():
    import os

    old = os.environ.pop("DEEPGRAM_API_KEY", None)
    try:
        with pytest.raises(ValueError, match="DEEPGRAM_API_KEY"):
            DeepgramBackend(on_partial=lambda t: None, on_final=lambda t: None)
    finally:
        if old is not None:
            os.environ["DEEPGRAM_API_KEY"] = old


def test_parse_event_invalid_json_returns_none_kind():
    """parse_event must return ParsedEvent(kind=None) for non-JSON input."""
    from tests._fake_ws import FakeWS

    ws = FakeWS()
    connect_fn, _ = fake_connect(ws)
    backend = DeepgramBackend(
        api_key=_FAKE_KEY,
        connect_fn=connect_fn,
        language="ko",
        on_partial=lambda t: None,
        on_final=lambda t: None,
    )
    result = backend.parse_event(b"\xff\x00 not json at all")
    assert result.kind is None


def test_parse_event_missing_alternatives_key_returns_none_kind():
    """parse_event must return ParsedEvent(kind=None) when 'alternatives' key is absent."""
    from tests._fake_ws import FakeWS

    ws = FakeWS()
    connect_fn, _ = fake_connect(ws)
    backend = DeepgramBackend(
        api_key=_FAKE_KEY,
        connect_fn=connect_fn,
        language="ko",
        on_partial=lambda t: None,
        on_final=lambda t: None,
    )
    # 'channel' dict has no 'alternatives' key → KeyError
    result = backend.parse_event(
        json.dumps({"type": "Results", "channel": {}, "is_final": True})
    )
    assert result.kind is None


def test_build_connect_no_language_param_when_language_empty():
    """build_connect() must NOT add 'language' to the URL when language is empty (branch 50->52)."""
    connect_fn, captured = fake_connect(FakeWS())
    backend = DeepgramBackend(
        api_key=_FAKE_KEY,
        connect_fn=connect_fn,
        language="",   # falsy → if self.language: branch is skipped
        on_partial=lambda t: None,
        on_final=lambda t: None,
    )
    info = backend.build_connect()
    assert "language" not in info.url


def test_parse_event_empty_alternatives_list_returns_none_kind():
    """parse_event must return ParsedEvent(kind=None) when alternatives list is empty."""
    from tests._fake_ws import FakeWS

    ws = FakeWS()
    connect_fn, _ = fake_connect(ws)
    backend = DeepgramBackend(
        api_key=_FAKE_KEY,
        connect_fn=connect_fn,
        language="ko",
        on_partial=lambda t: None,
        on_final=lambda t: None,
    )
    # alternatives list is empty → IndexError
    result = backend.parse_event(
        json.dumps({"type": "Results", "channel": {"alternatives": []}, "is_final": True})
    )
    assert result.kind is None


@pytest.mark.asyncio
async def test_stop_stream_sends_close_stream_frame():
    """stop_stream() must send {"type":"CloseStream"} before closing the socket.

    Per Deepgram docs, omitting CloseStream causes the server to discard
    audio that has not yet been transcribed.  The frame must be the last
    outbound message before ws.close() is called.
    """
    ws = FakeWS()
    backend, _ = _make(ws, [], [])
    await backend.start_stream()
    pcm = b"\x00\x01" * 160
    await backend.feed_audio(pcm)
    await backend.stop_stream()

    # The CloseStream JSON frame must appear after the audio frame.
    close_frames = [
        msg for msg in ws.sent if isinstance(msg, str) and json.loads(msg).get("type") == "CloseStream"
    ]
    assert len(close_frames) == 1, f"Expected exactly 1 CloseStream frame, got {ws.sent}"
    # CloseStream must come after the audio payload (raw bytes).
    audio_idx = next(i for i, m in enumerate(ws.sent) if isinstance(m, bytes))
    close_idx = next(i for i, m in enumerate(ws.sent) if isinstance(m, str) and json.loads(m).get("type") == "CloseStream")
    assert close_idx > audio_idx, "CloseStream frame must follow the audio frame"
