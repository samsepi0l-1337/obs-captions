from __future__ import annotations

from obs_captions.stt.base import STTBackend, Transcript


class FakeBackend(STTBackend):
    """Test/demo-only STT backend with manual transcript emission."""

    async def start_stream(self) -> None:
        return None

    async def feed_audio(self, pcm16: bytes) -> None:
        _ = pcm16
        return None

    async def flush(self) -> None:
        return None

    async def stop_stream(self) -> None:
        return None

    def emit_partial(self, text: str) -> None:
        self.on_partial(Transcript(text=text, is_final=False, lang=self.language))

    def emit_final(self, text: str) -> None:
        self.on_final(Transcript(text=text, is_final=True, lang=self.language))
