from __future__ import annotations

import base64
import io
import os
import wave

import httpx

from obs_captions.stt.utterance import UtteranceBackend

_ENDPOINT = "https://openrouter.ai/api/v1/audio/transcriptions"
_DEFAULT_MODEL = "openai/whisper-large-v3-turbo"


def _pcm16_to_wav_bytes(pcm16: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # 16-bit = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16)
    return buf.getvalue()


class OpenRouterBackend(UtteranceBackend):
    """Utterance-mode STT via OpenRouter audio transcriptions API (JSON/base64)."""

    def __init__(
        self,
        *,
        model: str = _DEFAULT_MODEL,
        api_key: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.model = model
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY") or ""
        if not self._api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required for OpenRouterBackend. "
                "Set it in .env or pass api_key=."
            )
        self._http_client = http_client
        self._owns_client = http_client is None

    async def _client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient()
        return self._http_client

    async def stop_stream(self) -> None:
        await super().stop_stream()
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def transcribe(self, pcm16: bytes, language: str) -> str:
        wav_bytes = _pcm16_to_wav_bytes(pcm16, self.sample_rate)
        b64_audio = base64.b64encode(wav_bytes).decode("ascii")
        payload: dict[str, object] = {
            "model": self.model,
            "input_audio": {"data": b64_audio, "format": "wav"},
        }
        if language:
            payload["language"] = language
        client = await self._client()
        resp = await client.post(
            _ENDPOINT,
            json=payload,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=30.0,
        )
        resp.raise_for_status()
        return str(resp.json().get("text", ""))
