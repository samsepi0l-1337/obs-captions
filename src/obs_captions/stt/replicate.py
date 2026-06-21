from __future__ import annotations

import asyncio
import base64
import inspect
import io
import os
import wave
from collections.abc import Callable

import httpx

from obs_captions.stt.utterance import UtteranceBackend

_PREDICTIONS_URL = "https://api.replicate.com/v1/predictions"
_DEFAULT_MODEL = "openai/whisper"
_DEFAULT_VERSION = "e39e354773466b955265e969568deb7da217804d58f9a5274ffd17e"
_POLL_INTERVAL = 1.0
_MAX_POLLS = 120


def _pcm16_to_wav_bytes(pcm16: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16)
    return buf.getvalue()


class ReplicateBackend(UtteranceBackend):
    """Utterance-mode STT via Replicate predictions API (async polling)."""

    def __init__(
        self,
        *,
        model: str = _DEFAULT_MODEL,
        version: str = _DEFAULT_VERSION,
        api_key: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        sleep_fn: Callable[[float], object] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.model = model
        self.version = version
        self._api_key = api_key or os.environ.get("REPLICATE_API_TOKEN") or ""
        if not self._api_key:
            raise ValueError(
                "REPLICATE_API_TOKEN is required for ReplicateBackend. "
                "Set it in .env or pass api_key=."
            )
        self._http_client = http_client
        self._owns_client = http_client is None
        # Injectable sleep for testing; defaults to asyncio.sleep
        self._sleep_fn: Callable[[float], object] = sleep_fn or asyncio.sleep

    async def _client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient()
        return self._http_client

    async def stop_stream(self) -> None:
        await super().stop_stream()
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def transcribe(self, pcm16: bytes, language: str) -> str:
        wav_bytes = _pcm16_to_wav_bytes(pcm16, self.sample_rate)
        b64_audio = base64.b64encode(wav_bytes).decode("ascii")
        data_uri = f"data:audio/wav;base64,{b64_audio}"

        client = await self._client()
        create_resp = await client.post(
            _PREDICTIONS_URL,
            json={
                "version": self.version,
                "input": {
                    "audio": data_uri,
                    "language": language or "ko",
                },
            },
            headers=self._auth_headers(),
            timeout=30.0,
        )
        create_resp.raise_for_status()
        prediction = create_resp.json()
        prediction_id: str = prediction["id"]
        poll_url = f"{_PREDICTIONS_URL}/{prediction_id}"

        for _ in range(_MAX_POLLS):
            result = self._sleep_fn(_POLL_INTERVAL)
            if inspect.isawaitable(result):
                await result
            poll_resp = await client.get(
                poll_url,
                headers=self._auth_headers(),
                timeout=30.0,
            )
            poll_resp.raise_for_status()
            data = poll_resp.json()
            status = data.get("status", "")
            if status == "succeeded":
                output = data.get("output") or {}
                if isinstance(output, dict):
                    return str(output.get("transcription", "") or output.get("text", ""))
                return str(output)
            if status in ("failed", "canceled"):
                raise RuntimeError(
                    f"Replicate prediction {prediction_id} {status}: {data.get('error')}"
                )

        raise TimeoutError(
            f"Replicate prediction {prediction_id} did not complete after {_MAX_POLLS} polls"
        )
