from __future__ import annotations

import json
import os
from urllib.parse import urlencode

from obs_captions.stt.streaming import ConnectInfo, ParsedEvent, StreamingBackend, header_dict

_BASE_URL = "wss://api.x.ai/v1/stt"
_DEFAULT_MODEL = "grok-transcribe"


class XaiBackend(StreamingBackend):
    """xAI Grok ``grok-transcribe`` streaming STT over websocket.

    Doc source: docs.x.ai/developers/model-capabilities/audio/speech-to-text.
    URL ``wss://api.x.ai/v1/stt`` (config via query params, no setup frame);
    ``Bearer XAI_API_KEY``; audio sent as RAW binary PCM16 frames (no base64);
    ``transcript.partial`` → on_partial (full hypothesis), ``transcript.done``
    → on_final. Server emits ``transcript.created`` when ready (ignored).
    """

    def __init__(
        self,
        *,
        model: str = _DEFAULT_MODEL,
        api_key: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.model = model
        self._api_key = api_key or os.environ.get("XAI_API_KEY") or ""
        if not self._api_key:
            raise ValueError(
                "XAI_API_KEY is required for XaiBackend. Set it in .env or pass api_key=."
            )

    def build_connect(self) -> ConnectInfo:
        params = {
            "sample_rate": str(self.sample_rate),
            "encoding": "pcm",
            "interim_results": "true",
        }
        if self.language:
            params["language"] = self.language
        url = f"{_BASE_URL}?{urlencode(params)}"
        headers = header_dict(Authorization=f"Bearer {self._api_key}")
        return ConnectInfo(url=url, headers=headers)

    def encode_audio(self, pcm16: bytes) -> str | bytes:
        # Raw binary frame — no base64, no JSON wrapper.
        return pcm16

    def parse_event(self, message: str | bytes) -> ParsedEvent:
        try:
            data = json.loads(message)
        except (ValueError, TypeError):
            return ParsedEvent(kind=None)
        event_type = data.get("type")
        if event_type == "transcript.partial":
            text = str(data.get("text", ""))
            # speech_final means the utterance is complete -> commit it.
            if data.get("speech_final"):
                return ParsedEvent(kind="final", text=text)
            return ParsedEvent(kind="partial", text=text)
        if event_type == "transcript.done":
            return ParsedEvent(kind="final", text=str(data.get("text", "")))
        return ParsedEvent(kind=None)
