from __future__ import annotations

import base64
import os

import httpx

from obs_captions.stt.utterance import UtteranceBackend, _pcm16_to_wav_bytes

_ENDPOINT = "https://openrouter.ai/api/v1/audio/transcriptions"
_DEFAULT_MODEL = "openai/whisper-large-v3-turbo"


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
        super().__init__(http_client=http_client, **kwargs)  # type: ignore[arg-type]
        self.model = model
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY") or ""
        if not self._api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required for OpenRouterBackend. "
                "Set it in .env or pass api_key=."
            )

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
