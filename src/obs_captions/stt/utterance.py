from __future__ import annotations

import io
import wave
from abc import abstractmethod

import httpx

from obs_captions.stt.base import STTBackend, Transcript


def _pcm16_to_wav_bytes(pcm16: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """Wrap raw PCM16 mono bytes in a WAV container (16-bit little-endian)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16)
    return buf.getvalue()


class UtteranceBackend(STTBackend):
    """Base for non-streaming (utterance/batch) STT providers.

    The VAD segmenter calls feed_audio() during speech and flush() at silence.
    flush() transcribes the accumulated buffer and emits on_final.

    Subclasses that talk to an HTTP provider get a lazily-created, owned
    ``httpx.AsyncClient`` (closed on stop_stream) for free; an injected client is
    treated as externally owned and left open.
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._buffer = bytearray()
        self._http_client = http_client
        self._owns_client = http_client is None

    async def start_stream(self) -> None:
        """No persistent stream needed for batch providers."""
        self._buffer.clear()

    async def feed_audio(self, pcm16: bytes) -> None:
        """Accumulate PCM16 bytes for the current utterance."""
        self._buffer.extend(pcm16)

    async def flush(self) -> None:
        """Transcribe buffered audio and emit on_final; no-op if buffer empty."""
        if not self._buffer:
            return
        pcm16 = bytes(self._buffer)
        self._buffer.clear()
        self.on_partial(Transcript(text="…", is_final=False, lang=self.language))
        text = await self.transcribe(pcm16, self.language)
        text = text.strip()
        if text:
            self.on_final(Transcript(text=text, is_final=True, lang=self.language))

    async def _client(self) -> httpx.AsyncClient:
        """Return the shared HTTP client, creating an owned one on first use."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient()
        return self._http_client

    async def stop_stream(self) -> None:
        """Flush remaining audio, then close the owned HTTP client if any."""
        if self._buffer:
            await self.flush()
        self._buffer.clear()
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    @abstractmethod
    async def transcribe(self, pcm16: bytes, language: str) -> str:
        """Send PCM16 bytes to the provider and return transcribed text."""
