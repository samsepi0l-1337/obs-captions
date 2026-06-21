from __future__ import annotations

import asyncio
import inspect
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any


from obs_captions.audio.capture import PCM16_SAMPLE_RATE, pcm16_to_float32
from obs_captions.stt.base import STTBackend, Transcript, local_agreement

TranscribeFn = Callable[[bytes], str | Awaitable[str]]


class LocalWhisperBackend(STTBackend):
    def __init__(
        self,
        *,
        language: str = "ko",
        sample_rate: int = PCM16_SAMPLE_RATE,
        on_partial: Callable[[Transcript], None],
        on_final: Callable[[Transcript], None],
        model_size: str = "small",
        cpu_threads: int = 1,
        partial_interval_ms: int = 500,
        transcribe_fn: TranscribeFn | None = None,
    ) -> None:
        super().__init__(
            language=language,
            sample_rate=sample_rate,
            on_partial=on_partial,
            on_final=on_final,
        )
        self.model_size = model_size
        self.cpu_threads = _bounded_cpu_threads(cpu_threads)
        self.partial_interval_ms = partial_interval_ms
        self._transcribe_fn = transcribe_fn
        self._model: Any | None = None
        self._buffer = bytearray()
        self._started = False
        self._last_partial_at = 0.0
        self._previous_tokens: list[str] = []
        self._last_confirmed_len = 0
        self._latest_text = ""
        self.confirmed_text = ""

    async def start_stream(self) -> None:
        if self._started:
            return
        if self._transcribe_fn is None:
            self._model = await asyncio.to_thread(self._load_model)
            self._transcribe_fn = self._transcribe_with_model
        self._started = True

    async def feed_audio(self, pcm16: bytes) -> None:
        self._ensure_started()
        if not pcm16:
            return
        if not self._buffer:
            self.confirmed_text = ""
        self._buffer.extend(pcm16)
        now = time.monotonic()
        due = self.partial_interval_ms <= 0 or (
            now - self._last_partial_at >= self.partial_interval_ms / 1000
        )
        if due:
            await self._emit_partial_from_buffer()

    async def flush(self) -> None:
        self._ensure_started()
        if not self._buffer:
            self._reset_segment()
            return
        if not self._latest_text:
            await self._emit_partial_from_buffer()
        tokens = tokenize_text(self._latest_text)
        text = detokenize_text(tokens[self._last_confirmed_len :], source_text=self._latest_text)
        if text:
            self.on_final(Transcript(text=text, is_final=True, lang=self.language))
        self._reset_segment()

    async def stop_stream(self) -> None:
        if not self._started:
            return
        if self._buffer:
            await self.flush()
        self._started = False

    async def _emit_partial_from_buffer(self) -> None:
        text = (await self._transcribe(bytes(self._buffer))).strip()
        self._last_partial_at = time.monotonic()
        if not text:
            return
        current_tokens = tokenize_text(text)
        agreed = local_agreement(self._previous_tokens, current_tokens, n=2)
        if len(agreed) > self._last_confirmed_len:
            for token in agreed[self._last_confirmed_len :]:
                self.on_final(Transcript(text=token, is_final=True, lang=self.language))
            self._last_confirmed_len = len(agreed)

        if self._last_confirmed_len:
            self.confirmed_text = detokenize_text(
                current_tokens[: self._last_confirmed_len], source_text=text
            )
        self._previous_tokens = current_tokens
        self._latest_text = text
        tail = detokenize_text(current_tokens[self._last_confirmed_len :], source_text=text)
        self.on_partial(Transcript(text=tail, is_final=False, lang=self.language))

    async def _transcribe(self, pcm16: bytes) -> str:
        if self._transcribe_fn is None:
            raise RuntimeError("LocalWhisperBackend has no transcriber")
        value = self._transcribe_fn(pcm16)
        if inspect.isawaitable(value):
            return str(await value)
        return str(value)

    def _load_model(self) -> Any:
        from faster_whisper import WhisperModel

        return WhisperModel(
            self.model_size,
            device="cpu",
            compute_type="int8",
            cpu_threads=self.cpu_threads,
        )

    def _transcribe_with_model(self, pcm16: bytes) -> str:
        if self._model is None:
            raise RuntimeError("Whisper model is not loaded")
        audio = pcm16_to_float32(pcm16)
        segments, _info = self._model.transcribe(
            audio,
            language=self.language,
            vad_filter=False,
            beam_size=1,
        )
        return "".join(segment.text for segment in segments).strip()

    def _ensure_started(self) -> None:
        if not self._started:
            raise RuntimeError("LocalWhisperBackend.start_stream() must be called first")

    def _reset_segment(self) -> None:
        self._buffer.clear()
        self._previous_tokens = []
        self._last_confirmed_len = 0
        self._latest_text = ""
        self._last_partial_at = 0.0


def tokenize_text(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    words = stripped.split()
    if len(words) > 1:
        return words
    return list(stripped)


def detokenize_text(tokens: list[str], *, source_text: str) -> str:
    if " " in source_text:
        return " ".join(tokens)
    return "".join(tokens)


def _bounded_cpu_threads(requested: int) -> int:
    available = os.cpu_count() or 1
    return max(1, min(int(requested), available, 4))
