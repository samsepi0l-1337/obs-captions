from __future__ import annotations

import asyncio
import contextlib

import pytest

from obs_captions.cli import _capture_to_backend
from obs_captions.vad import VadEvent


class BlockingCapture:
    def __init__(self) -> None:
        self.started = False
        self.frame_consumed = asyncio.Event()
        self.release = asyncio.Event()

    def start(self) -> None:
        self.started = True

    async def frames(self):
        yield b"speech"
        self.frame_consumed.set()
        await self.release.wait()


class SpeechThenPendingSegmenter:
    def __init__(self) -> None:
        self.flush_calls = 0

    def process(self, pcm16: bytes) -> VadEvent:
        assert pcm16 == b"speech"
        return VadEvent(is_speech=True)

    def flush(self) -> tuple[int, int] | None:
        self.flush_calls += 1
        return (0, 100)


class RecordingBackend:
    def __init__(self) -> None:
        self.started = False
        self.fed: list[bytes] = []
        self.flush_calls = 0

    async def start_stream(self) -> None:
        self.started = True

    async def feed_audio(self, pcm16: bytes) -> None:
        self.fed.append(pcm16)

    async def flush(self) -> None:
        self.flush_calls += 1


@pytest.mark.asyncio
async def test_capture_to_backend_flushes_pending_segment_on_cancellation():
    capture = BlockingCapture()
    segmenter = SpeechThenPendingSegmenter()
    backend = RecordingBackend()
    task = asyncio.create_task(_capture_to_backend(capture, segmenter, backend))

    await asyncio.wait_for(capture.frame_consumed.wait(), timeout=1)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert capture.started is True
    assert backend.started is True
    assert backend.fed == [b"speech"]
    assert segmenter.flush_calls == 1
    assert backend.flush_calls == 1
