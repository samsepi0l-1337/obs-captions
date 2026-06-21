from __future__ import annotations

import base64
import json
import os

import numpy as np

from obs_captions.audio.capture import float32_to_pcm16, pcm16_to_float32, resample_linear
from obs_captions.stt.streaming import ConnectInfo, ParsedEvent, StreamingBackend, header_dict

_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
_DEFAULT_MODEL = "gpt-realtime-whisper"
_TARGET_RATE = 24000  # Realtime transcription sessions require 24 kHz PCM16.

_DELTA_EVENT = "conversation.item.input_audio_transcription.delta"
_COMPLETED_EVENT = "conversation.item.input_audio_transcription.completed"


class OpenAIRealtimeBackend(StreamingBackend):
    """OpenAI Realtime API transcription session over websocket.

    Doc source: developers.openai.com/api/docs/guides/realtime-transcription
    URL ``wss://api.openai.com/v1/realtime?intent=transcription``; Bearer auth;
    audio appended via ``input_audio_buffer.append`` (base64 24 kHz PCM16);
    partial event ``conversation.item.input_audio_transcription.delta`` (delta,
    accumulated) and final ``...completed`` (full transcript).
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
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY") or ""
        if not self._api_key:
            raise ValueError(
                "OPENAI_API_KEY is required for OpenAIRealtimeBackend. "
                "Set it in .env or pass api_key=."
            )

    def build_connect(self) -> ConnectInfo:
        headers = header_dict(
            Authorization=f"Bearer {self._api_key}",
            **{"OpenAI-Beta": "realtime=v1"},
        )
        return ConnectInfo(url=_URL, headers=headers)

    def initial_messages(self) -> list[str | bytes]:
        session: dict[str, object] = {
            "input_audio_format": "pcm16",
            "input_audio_transcription": {"model": self.model},
        }
        if self.language:
            session["input_audio_transcription"]["language"] = self.language  # type: ignore[index]
        return [json.dumps({"type": "transcription_session.update", "session": session})]

    def encode_audio(self, pcm16: bytes) -> str | bytes:
        audio = pcm16_to_float32(pcm16)
        resampled = resample_linear(audio, source_rate=self.sample_rate, target_rate=_TARGET_RATE)
        upsampled = float32_to_pcm16(np.asarray(resampled, dtype=np.float32))
        b64 = base64.b64encode(upsampled).decode("ascii")
        return json.dumps({"type": "input_audio_buffer.append", "audio": b64})

    def parse_event(self, message: str | bytes) -> ParsedEvent:
        try:
            data = json.loads(message)
        except (ValueError, TypeError):
            return ParsedEvent(kind=None)
        event_type = data.get("type")
        if event_type == _DELTA_EVENT:
            return ParsedEvent(kind="partial", text=str(data.get("delta", "")), is_delta=True)
        if event_type == _COMPLETED_EVENT:
            return ParsedEvent(kind="final", text=str(data.get("transcript", "")))
        return ParsedEvent(kind=None)
