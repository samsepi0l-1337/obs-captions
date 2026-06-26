from __future__ import annotations

import json
import os
from urllib.parse import urlencode

from obs_captions.stt.streaming import ConnectInfo, ParsedEvent, StreamingBackend, header_dict

_BASE_URL = "wss://api.deepgram.com/v1/listen"
_DEFAULT_MODEL = "nova-3"


class DeepgramBackend(StreamingBackend):
    """Deepgram live streaming STT over websocket.

    Doc source: developers.deepgram.com/docs/determining-your-audio-format-for-live-streaming-audio
    URL ``wss://api.deepgram.com/v1/listen`` (config via query params, no setup frame);
    ``Authorization: Token <DEEPGRAM_API_KEY>``; audio sent as RAW binary PCM16 frames
    (no base64, no JSON wrapper); server returns ``{"type":"Results","channel":
    {"alternatives":[{"transcript":"..."}]},"is_final":bool,"speech_final":bool}``.
    ``is_final=False`` → on_partial (full hypothesis); ``is_final=True`` or
    ``speech_final=True`` → on_final. Close: send ``{"type":"CloseStream"}``.
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
        self._api_key = api_key or os.environ.get("DEEPGRAM_API_KEY") or ""
        if not self._api_key:
            raise ValueError(
                "DEEPGRAM_API_KEY is required for DeepgramBackend. "
                "Set it in .env or pass api_key=."
            )

    def build_connect(self) -> ConnectInfo:
        params: dict[str, str] = {
            "model": self.model,
            "encoding": "linear16",
            "sample_rate": str(self.sample_rate),
            "channels": "1",
            "interim_results": "true",
            "punctuate": "true",
        }
        if self.language:
            params["language"] = self.language
        url = f"{_BASE_URL}?{urlencode(params)}"
        headers = header_dict(Authorization=f"Token {self._api_key}")
        return ConnectInfo(url=url, headers=headers)

    def encode_audio(self, pcm16: bytes) -> str | bytes:
        # Raw binary frame — no base64, no JSON wrapper.
        return pcm16

    def parse_event(self, message: str | bytes) -> ParsedEvent:
        try:
            data = json.loads(message)
        except (ValueError, TypeError):
            return ParsedEvent(kind=None)
        if data.get("type") != "Results":
            return ParsedEvent(kind=None)
        try:
            transcript = str(data["channel"]["alternatives"][0].get("transcript", ""))
        except (KeyError, IndexError, TypeError):
            return ParsedEvent(kind=None)
        if not transcript:
            return ParsedEvent(kind=None)
        is_final: bool = bool(data.get("is_final", False))
        speech_final: bool = bool(data.get("speech_final", False))
        if is_final or speech_final:
            return ParsedEvent(kind="final", text=transcript)
        return ParsedEvent(kind="partial", text=transcript)
