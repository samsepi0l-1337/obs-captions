from __future__ import annotations

import os

import httpx

from obs_captions.stt.utterance import UtteranceBackend, _pcm16_to_wav_bytes

_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
_DEFAULT_MODEL = "whisper-large-v3-turbo"


class GroqBackend(UtteranceBackend):
    """Utterance-mode STT via Groq audio transcriptions API (multipart/form-data).

    Source: https://console.groq.com/docs/speech-to-text
    Endpoint: POST https://api.groq.com/openai/v1/audio/transcriptions
    Auth: Authorization: Bearer <GROQ_API_KEY>
    Request: multipart/form-data — file (WAV), model, language, response_format=json
    Response: {"text": "..."}
    """

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
        self._api_key = api_key or os.environ.get("GROQ_API_KEY") or ""
        if not self._api_key:
            raise ValueError(
                "GROQ_API_KEY is required for GroqBackend. "
                "Set it in .env or pass api_key=."
            )

    async def transcribe(self, pcm16: bytes, language: str) -> str:
        wav_bytes = _pcm16_to_wav_bytes(pcm16, self.sample_rate)
        client = await self._client()
        resp = await client.post(
            _ENDPOINT,
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={"model": self.model, "language": language, "response_format": "json"},
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=30.0,
        )
        resp.raise_for_status()
        return str(resp.json().get("text", ""))
