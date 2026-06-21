from __future__ import annotations

from abc import abstractmethod

from obs_captions.stt.base import STTBackend, Transcript


class UtteranceBackend(STTBackend):
    """Base for non-streaming (utterance/batch) STT providers.

    The VAD segmenter calls feed_audio() during speech and flush() at silence.
    flush() transcribes the accumulated buffer and emits on_final.
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._buffer = bytearray()

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

    async def stop_stream(self) -> None:
        """Flush any remaining audio and clean up."""
        if self._buffer:
            await self.flush()
        self._buffer.clear()

    @abstractmethod
    async def transcribe(self, pcm16: bytes, language: str) -> str:
        """Send PCM16 bytes to the provider and return transcribed text."""
