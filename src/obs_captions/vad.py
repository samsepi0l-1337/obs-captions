from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from obs_captions.audio.capture import PCM16_SAMPLE_RATE, pcm16_to_float32


@dataclass(frozen=True)
class VadEvent:
    is_speech: bool
    segment: tuple[int, int] | None = None


class SileroVad:
    def __init__(
        self,
        *,
        model: Any | None = None,
        threshold: float = 0.5,
        sample_rate: int = PCM16_SAMPLE_RATE,
        window_size: int = 512,
    ) -> None:
        self.model = model or _load_silero_onnx_model()
        self.threshold = threshold
        self.sample_rate = sample_rate
        self.window_size = window_size

    def is_speech(self, pcm16: bytes) -> bool:
        frame = pcm16_to_float32(pcm16)
        probabilities = [
            _probability_to_float(_call_model(self.model, chunk, self.sample_rate))
            for chunk in _chunks(frame, self.window_size)
        ]
        return max(probabilities, default=0.0) >= self.threshold


class UtteranceSegmenter:
    def __init__(
        self,
        *,
        vad: SileroVad,
        frame_ms: int = 100,
        min_silence_ms: int = 500,
    ) -> None:
        if frame_ms <= 0 or min_silence_ms <= 0:
            raise ValueError("frame_ms and min_silence_ms must be positive")
        self.vad = vad
        self.frame_ms = frame_ms
        self.min_silence_frames = max(1, min_silence_ms // frame_ms)
        self._frame_index = 0
        self._speech_start: int | None = None
        self._last_speech_end: int | None = None
        self._silence_frames = 0

    def process(self, pcm16: bytes) -> VadEvent:
        expected_bytes = int(self.vad.sample_rate * self.frame_ms / 1000) * 2
        if len(pcm16) != expected_bytes:
            raise ValueError(f"expected {expected_bytes} bytes, got {len(pcm16)}")

        start_ms = self._frame_index * self.frame_ms
        end_ms = start_ms + self.frame_ms
        self._frame_index += 1

        is_speech = self.vad.is_speech(pcm16)
        if is_speech:
            if self._speech_start is None:
                self._speech_start = start_ms
            self._last_speech_end = end_ms
            self._silence_frames = 0
            return VadEvent(is_speech=True)

        if self._speech_start is None:
            return VadEvent(is_speech=False)

        self._silence_frames += 1
        if self._silence_frames < self.min_silence_frames:
            return VadEvent(is_speech=False)

        segment = (self._speech_start, self._last_speech_end or start_ms)
        self._speech_start = None
        self._last_speech_end = None
        self._silence_frames = 0
        return VadEvent(is_speech=False, segment=segment)

    def flush(self) -> tuple[int, int] | None:
        if self._speech_start is None:
            return None
        segment = (self._speech_start, self._last_speech_end or self._frame_index * self.frame_ms)
        self._speech_start = None
        self._last_speech_end = None
        self._silence_frames = 0
        return segment


def _load_silero_onnx_model() -> Any:
    from silero_vad import load_silero_vad

    return load_silero_vad(onnx=True)


def _probability_to_float(value: Any) -> float:
    if hasattr(value, "item"):
        return float(value.item())
    if isinstance(value, np.ndarray):
        return float(value.reshape(-1)[0])
    return float(value)


def _call_model(model: Any, frame: np.ndarray, sample_rate: int) -> Any:
    try:
        return model(frame, sample_rate)
    except AttributeError as exc:
        if "dim" not in str(exc):
            raise
        import torch

        return model(torch.from_numpy(frame), sample_rate)


def _chunks(frame: np.ndarray, window_size: int) -> list[np.ndarray]:
    if window_size <= 0:
        raise ValueError("window_size must be positive")
    if frame.size == 0:
        return [np.zeros(window_size, dtype=np.float32)]
    chunks: list[np.ndarray] = []
    for start in range(0, frame.size, window_size):
        chunk = frame[start : start + window_size]
        if chunk.size < window_size:
            chunk = np.pad(chunk, (0, window_size - chunk.size))
        chunks.append(np.ascontiguousarray(chunk, dtype=np.float32))
    return chunks
