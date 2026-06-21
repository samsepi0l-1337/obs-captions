from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Any

import numpy as np

PCM16_SAMPLE_RATE = 16000
DEFAULT_BLOCKSIZE = 1600


def float32_to_pcm16(samples: np.ndarray) -> bytes:
    mono = _as_mono_float32(samples)
    clipped = np.clip(mono, -1.0, 1.0)
    scaled = np.where(clipped < 0, clipped * 32768.0, clipped * 32767.0)
    return scaled.astype("<i2").tobytes()


def pcm16_to_float32(pcm16: bytes) -> np.ndarray:
    samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32)
    return samples / 32768.0


def resample_linear(samples: np.ndarray, *, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")

    mono = _as_mono_float32(samples)
    if source_rate == target_rate:
        return mono.copy()
    if mono.size == 0:
        return mono.copy()

    target_len = max(1, round(mono.size * target_rate / source_rate))
    source_positions = np.linspace(0, mono.size - 1, num=mono.size, dtype=np.float32)
    target_positions = np.linspace(0, mono.size - 1, num=target_len, dtype=np.float32)
    return np.interp(target_positions, source_positions, mono).astype(np.float32)


class MicCapture:
    def __init__(
        self,
        *,
        device: int | str | None = None,
        samplerate: int = PCM16_SAMPLE_RATE,
        channels: int = 1,
        dtype: str = "float32",
        blocksize: int = DEFAULT_BLOCKSIZE,
        target_samplerate: int = PCM16_SAMPLE_RATE,
        queue_maxsize: int = 64,
        stream_factory: Callable[..., Any] | None = None,
    ) -> None:
        if channels != 1:
            raise ValueError("MicCapture currently supports mono input only")
        self.device = device
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.blocksize = blocksize
        self.target_samplerate = target_samplerate
        self._stream_factory = stream_factory
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=queue_maxsize)
        self._stream: Any | None = None
        self._closed = False

    def start(self) -> None:
        if self._stream is not None:
            return
        self._loop = asyncio.get_running_loop()
        factory = self._stream_factory or _sounddevice_input_stream
        self._stream = factory(
            samplerate=self.samplerate,
            channels=self.channels,
            dtype=self.dtype,
            blocksize=self.blocksize,
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()

    async def stop(self) -> None:
        if self._stream is None:
            return
        stream = self._stream
        self._stream = None
        stream.stop()
        stream.close()
        self._closed = True
        await self._put(None)

    async def read(self) -> bytes:
        frame = await self._queue.get()
        if frame is None:
            raise EOFError("microphone capture stopped")
        return frame

    async def frames(self) -> AsyncIterator[bytes]:
        while True:
            try:
                yield await self.read()
            except EOFError:
                return

    def _callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        _ = frames, time_info, status
        samples = _as_mono_float32(indata)
        if self.samplerate != self.target_samplerate:
            samples = resample_linear(
                samples, source_rate=self.samplerate, target_rate=self.target_samplerate
            )
        pcm16 = float32_to_pcm16(samples)
        loop = self._loop
        if loop is None or self._closed:
            return
        loop.call_soon_threadsafe(self._put_nowait_drop_oldest, pcm16)

    def _put_nowait_drop_oldest(self, frame: bytes | None) -> None:
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
            self._queue.put_nowait(frame)

    async def _put(self, frame: bytes | None) -> None:
        self._put_nowait_drop_oldest(frame)


def _as_mono_float32(samples: np.ndarray) -> np.ndarray:
    array = np.asarray(samples, dtype=np.float32)
    if array.ndim == 2:
        if array.shape[1] == 0:
            return np.array([], dtype=np.float32)
        array = array[:, 0]
    return np.ascontiguousarray(array.reshape(-1), dtype=np.float32)


def _sounddevice_input_stream(**kwargs: Any) -> Any:
    import sounddevice as sd

    return sd.InputStream(**kwargs)
