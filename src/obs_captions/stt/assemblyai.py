from __future__ import annotations

import json
import os
from urllib.parse import urlencode

from obs_captions.stt.streaming import ConnectInfo, ParsedEvent, StreamingBackend, header_dict

_BASE_URL = "wss://streaming.assemblyai.com/v3/ws"
_DEFAULT_MODEL = "universal-streaming-english"


class AssemblyAIRealtimeBackend(StreamingBackend):
    """AssemblyAI Universal Streaming v3 real-time STT over websocket.

    Doc source: assemblyai.com/docs/streaming/universal-streaming
    Endpoint ``wss://streaming.assemblyai.com/v3/ws`` with query params
    ``sample_rate``, ``encoding=pcm_s16le``, ``speech_model``;
    ``Authorization: <api_key>`` header (no Bearer prefix);
    audio sent as raw PCM16 binary; server sends ``Begin``, ``Turn``,
    and ``Termination`` JSON messages.

    Turn protocol:
      ``end_of_turn=False`` → on_partial with full current hypothesis.
      ``end_of_turn=True``  → on_final with the committed segment.

    Language note: the default model (``universal-streaming-english``) is
    ENGLISH-ONLY. Pass ``model="universal-streaming-multilingual"`` for
    multilingual use. Korean (ko) support for the streaming API is NOT
    explicitly confirmed in AssemblyAI docs — the multilingual streaming
    model's supported-language list does not enumerate Korean. AssemblyAI's
    batch transcription supports Korean, but streaming support is unverified.
    Real smoke-testing requires a live ASSEMBLYAI_API_KEY.
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
        self._api_key = api_key or os.environ.get("ASSEMBLYAI_API_KEY") or ""
        if not self._api_key:
            raise ValueError(
                "ASSEMBLYAI_API_KEY is required for AssemblyAIRealtimeBackend. "
                "Set it in .env or pass api_key=."
            )

    def build_connect(self) -> ConnectInfo:
        params = urlencode(
            {
                "sample_rate": self.sample_rate,
                "encoding": "pcm_s16le",
                "speech_model": self.model,
            }
        )
        url = f"{_BASE_URL}?{params}"
        headers = header_dict(Authorization=self._api_key)
        return ConnectInfo(url=url, headers=headers)

    def encode_audio(self, pcm16: bytes) -> str | bytes:
        """PCM16 bytes sent raw — AssemblyAI v3 streaming accepts binary directly."""
        return pcm16

    def parse_event(self, message: str | bytes) -> ParsedEvent:
        try:
            data = json.loads(message)
        except (ValueError, TypeError):
            return ParsedEvent(kind=None)
        msg_type = data.get("type")
        if msg_type == "Turn":
            transcript = str(data.get("transcript", ""))
            if data.get("end_of_turn"):
                return ParsedEvent(kind="final", text=transcript)
            return ParsedEvent(kind="partial", text=transcript)
        return ParsedEvent(kind=None)
