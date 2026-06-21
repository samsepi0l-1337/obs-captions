from __future__ import annotations

import pytest

from obs_captions.stt.base import Transcript
from obs_captions.stt.streaming import ConnectInfo, ParsedEvent, StreamingBackend
from tests._fake_ws import FakeWS, fake_connect, wait_for


class _StubStreaming(StreamingBackend):
    """Minimal concrete StreamingBackend driven by JSON-ish dict events."""

    def build_connect(self) -> ConnectInfo:
        return ConnectInfo(url="wss://stub/test", headers={"X-Auth": "k"})

    def initial_messages(self) -> list[str | bytes]:
        return ["setup"]

    def encode_audio(self, pcm16: bytes) -> str | bytes:
        return f"audio:{len(pcm16)}"

    def parse_event(self, message: str | bytes) -> ParsedEvent:
        text = message.decode() if isinstance(message, bytes) else message
        if text.startswith("delta:"):
            return ParsedEvent(kind="partial", text=text[6:], is_delta=True)
        if text.startswith("partial:"):
            return ParsedEvent(kind="partial", text=text[8:])
        if text.startswith("final:"):
            return ParsedEvent(kind="final", text=text[6:])
        return ParsedEvent(kind=None)


def _make(ws: FakeWS, collect_partial, collect_final):
    connect_fn, captured = fake_connect(ws)
    backend = _StubStreaming(
        connect_fn=connect_fn,
        language="ko",
        on_partial=collect_partial,
        on_final=collect_final,
    )
    return backend, captured


@pytest.mark.asyncio
async def test_start_stream_connects_and_sends_setup():
    ws = FakeWS()
    backend, captured = _make(ws, lambda t: None, lambda t: None)
    await backend.start_stream()
    try:
        assert captured["url"] == "wss://stub/test"
        assert captured["headers"]["X-Auth"] == "k"
        assert ws.sent == ["setup"]
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_feed_audio_sends_encoded_message():
    ws = FakeWS()
    backend, _ = _make(ws, lambda t: None, lambda t: None)
    await backend.start_stream()
    try:
        await backend.feed_audio(b"\x00\x00\x00\x00")
        assert "audio:4" in ws.sent
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_delta_partials_accumulate_to_full_hypothesis():
    partials: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, partials.append, lambda t: None)
    await backend.start_stream()
    try:
        ws.push("delta:안녕")
        ws.push("delta:하세요")
        await wait_for(lambda: len(partials) >= 2)
        assert partials[-1].text == "안녕하세요"
        assert partials[-1].is_final is False
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_full_hypothesis_partial_replaces():
    partials: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, partials.append, lambda t: None)
    await backend.start_stream()
    try:
        ws.push("partial:hello")
        ws.push("partial:hello world")
        await wait_for(lambda: len(partials) >= 2)
        assert partials[-1].text == "hello world"
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_final_event_emits_on_final_and_resets_accum():
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    ws = FakeWS()
    backend, _ = _make(ws, partials.append, finals.append)
    await backend.start_stream()
    try:
        ws.push("delta:hi")
        ws.push("final:hi there")
        await wait_for(lambda: len(finals) >= 1)
        assert finals[-1].text == "hi there"
        assert finals[-1].is_final is True
        # After final, a new delta starts a fresh hypothesis.
        ws.push("delta:next")
        await wait_for(lambda: partials and partials[-1].text == "next")
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_stop_stream_closes_socket():
    ws = FakeWS()
    backend, _ = _make(ws, lambda t: None, lambda t: None)
    await backend.start_stream()
    await backend.stop_stream()
    assert ws.closed is True


@pytest.mark.asyncio
async def test_reconnect_on_recv_failure():
    sockets: list[FakeWS] = []

    class _Flaky(_StubStreaming):
        async def _open(self) -> None:  # type: ignore[override]
            new_ws = FakeWS()
            sockets.append(new_ws)
            self._ws = new_ws

    backend = _Flaky(
        connect_fn=None,
        language="ko",
        on_partial=lambda t: None,
        on_final=lambda t: None,
        backoff_base=0.0,
        sleep_fn=_no_sleep,
    )
    await backend.start_stream()
    try:
        # First socket raises on recv -> base should reconnect and open a 2nd.
        await sockets[0].close()
        sockets[0].push("__boom__")  # ignored kind, keeps loop alive

        async def _raise() -> str:
            raise ConnectionError("dropped")

        sockets[0].recv = _raise  # type: ignore[assignment]
        await wait_for(lambda: len(sockets) >= 2, timeout=2.0)
    finally:
        await backend.stop_stream()


async def _no_sleep(_seconds: float) -> None:
    return None
