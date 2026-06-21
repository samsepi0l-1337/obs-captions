from __future__ import annotations

import base64
import json
import os

from obs_captions.stt.streaming import ConnectInfo, ParsedEvent, StreamingBackend, header_dict

_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
_DEFAULT_MODEL = "scribe_v2_realtime"


class ElevenLabsRealtimeBackend(StreamingBackend):
    """ElevenLabs Scribe v2 Realtime STT over websocket.

    Doc source: elevenlabs-python src/elevenlabs/realtime/scribe.py +
    README STT table. URL ``wss://api.elevenlabs.io/v1/speech-to-text/realtime``;
    ``xi-api-key`` header; ``model_id=scribe_v2_realtime``; audio sent as
    ``input_audio_chunk`` (base64 16 kHz PCM16); ``partial_transcript`` →
    on_partial (full hypothesis), ``committed_transcript`` → on_final.
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
        self._api_key = api_key or os.environ.get("ELEVENLABS_API_KEY") or ""
        if not self._api_key:
            raise ValueError(
                "ELEVENLABS_API_KEY is required for ElevenLabsRealtimeBackend. "
                "Set it in .env or pass api_key=."
            )

    def build_connect(self) -> ConnectInfo:
        headers = header_dict(**{"xi-api-key": self._api_key})
        return ConnectInfo(url=_URL, headers=headers)

    def initial_messages(self) -> list[str | bytes]:
        config: dict[str, object] = {
            "model_id": self.model,
            "audio_format": "pcm_16000",
            "sample_rate": self.sample_rate,
        }
        if self.language:
            config["language_code"] = self.language
        return [json.dumps({"type": "session_config", **config})]

    def encode_audio(self, pcm16: bytes) -> str | bytes:
        b64 = base64.b64encode(pcm16).decode("ascii")
        return json.dumps({"type": "input_audio_chunk", "audio_chunk": b64})

    def parse_event(self, message: str | bytes) -> ParsedEvent:
        try:
            data = json.loads(message)
        except (ValueError, TypeError):
            return ParsedEvent(kind=None)
        event_type = data.get("type")
        if event_type == "partial_transcript":
            return ParsedEvent(kind="partial", text=_extract_text(data))
        if event_type == "committed_transcript":
            return ParsedEvent(kind="final", text=_extract_text(data))
        return ParsedEvent(kind=None)


def _extract_text(data: dict[str, object]) -> str:
    transcript = data.get("transcript")
    if isinstance(transcript, dict):
        return str(transcript.get("text", ""))
    if transcript is not None:
        return str(transcript)
    return str(data.get("text", ""))
