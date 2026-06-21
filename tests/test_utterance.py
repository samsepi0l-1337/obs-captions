from __future__ import annotations

import pytest

from obs_captions.stt.base import Transcript
from obs_captions.stt.utterance import UtteranceBackend


class _FakeUtterance(UtteranceBackend):
    """Concrete subclass for testing UtteranceBackend behavior."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.transcribe_calls: list[tuple[bytes, str]] = []
        self._transcribe_result = "hello"

    async def transcribe(self, pcm16: bytes, language: str) -> str:
        self.transcribe_calls.append((pcm16, language))
        return self._transcribe_result


def _noop(t: Transcript) -> None:
    pass


def _make_backend(on_partial=None, on_final=None, language: str = "ko") -> _FakeUtterance:
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    return _FakeUtterance(
        language=language,
        on_partial=on_partial or partials.append,
        on_final=on_final or finals.append,
    )


@pytest.mark.asyncio
async def test_feed_audio_accumulates_buffer():
    backend = _make_backend()
    await backend.start_stream()
    await backend.feed_audio(b"\x00\x01")
    await backend.feed_audio(b"\x02\x03")
    assert bytes(backend._buffer) == b"\x00\x01\x02\x03"


@pytest.mark.asyncio
async def test_flush_calls_transcribe_with_full_buffer():
    calls: list[tuple[bytes, str]] = []
    finals: list[Transcript] = []

    backend = _FakeUtterance(
        language="ko",
        on_partial=_noop,
        on_final=finals.append,
    )
    backend.transcribe_calls = calls
    await backend.start_stream()
    await backend.feed_audio(b"\x00\x01\x02\x03")
    await backend.flush()

    assert len(calls) == 1
    assert calls[0][0] == b"\x00\x01\x02\x03"
    assert calls[0][1] == "ko"


@pytest.mark.asyncio
async def test_flush_emits_on_final():
    finals: list[Transcript] = []
    backend = _FakeUtterance(
        language="ko",
        on_partial=_noop,
        on_final=finals.append,
    )
    backend._transcribe_result = "테스트"
    await backend.start_stream()
    await backend.feed_audio(b"\x00\x01")
    await backend.flush()

    assert len(finals) == 1
    assert finals[0].text == "테스트"
    assert finals[0].is_final is True


@pytest.mark.asyncio
async def test_flush_empty_buffer_is_noop():
    calls: list[tuple[bytes, str]] = []
    finals: list[Transcript] = []
    backend = _FakeUtterance(
        language="ko",
        on_partial=_noop,
        on_final=finals.append,
    )
    backend.transcribe_calls = calls
    await backend.start_stream()
    await backend.flush()

    assert calls == []
    assert finals == []


@pytest.mark.asyncio
async def test_flush_clears_buffer():
    backend = _make_backend()
    await backend.start_stream()
    await backend.feed_audio(b"\x00\x01")
    await backend.flush()
    assert bytes(backend._buffer) == b""


@pytest.mark.asyncio
async def test_flush_emits_partial_while_transcribing():
    partials: list[Transcript] = []
    backend = _FakeUtterance(
        language="ko",
        on_partial=partials.append,
        on_final=_noop,
    )
    await backend.start_stream()
    await backend.feed_audio(b"\x00\x01")
    await backend.flush()

    assert any(t.text == "…" for t in partials)
