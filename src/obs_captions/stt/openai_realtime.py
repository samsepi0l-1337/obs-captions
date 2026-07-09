from __future__ import annotations

import base64
import json
import os
from typing import Literal
from urllib.parse import quote

import numpy as np

from obs_captions.audio.capture import float32_to_pcm16, pcm16_to_float32, resample_linear
from obs_captions.stt.streaming import ConnectInfo, ParsedEvent, StreamingBackend, header_dict

_DEFAULT_MODEL = "gpt-realtime-whisper"
_TARGET_RATE = 24000  # Realtime sessions require 24 kHz PCM16.

_WHISPER_MODEL = "gpt-realtime-whisper"
_TRANSLATE_MODEL = "gpt-realtime-translate"
_REALTIME_21_MODEL = "gpt-realtime-2.1"
_SUPPORTED_MODELS = frozenset({_WHISPER_MODEL, _TRANSLATE_MODEL, _REALTIME_21_MODEL})

_DELTA_EVENT = "conversation.item.input_audio_transcription.delta"
_COMPLETED_EVENT = "conversation.item.input_audio_transcription.completed"
_OUTPUT_TRANSCRIPT_DELTA = "response.output_audio_transcript.delta"
_OUTPUT_TRANSCRIPT_DONE = "response.output_audio_transcript.done"
_TRANSLATE_OUTPUT_DELTA = "session.output_transcript.delta"

_Delay = Literal["minimal", "low", "medium", "high", "xhigh"]


class OpenAIRealtimeBackend(StreamingBackend):
    """OpenAI Realtime API over websocket for caption-oriented sessions.

    Supported models (``providers.openai.model``):

    - ``gpt-realtime-whisper`` — GA transcription session
      (``/v1/realtime``, ``session.type=transcription``).
    - ``gpt-realtime-translate`` — translation session
      (``/v1/realtime/translations``); captions use output transcript deltas.
    - ``gpt-realtime-2.1`` — realtime voice session with input transcription
      enabled for captions (``/v1/realtime?model=...``).

    Doc sources: developers.openai.com/api/docs/guides/realtime,
    realtime-transcription, realtime-translation, realtime-conversations.
    """

    def __init__(
        self,
        *,
        model: str = _DEFAULT_MODEL,
        api_key: str | None = None,
        delay: str | None = None,
        target_language: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        if model not in _SUPPORTED_MODELS:
            raise ValueError(
                f"Unsupported OpenAI realtime model: {model!r}. "
                f"Supported: {', '.join(sorted(_SUPPORTED_MODELS))}."
            )
        self.model = model
        self.delay = delay
        self.target_language = target_language or self.language or "en"
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY") or ""
        if not self._api_key:
            raise ValueError(
                "OPENAI_API_KEY is required for OpenAIRealtimeBackend. "
                "Set it in .env or pass api_key=."
            )

    def build_connect(self) -> ConnectInfo:
        headers = header_dict(Authorization=f"Bearer {self._api_key}")
        if self.model == _TRANSLATE_MODEL:
            url = (
                "wss://api.openai.com/v1/realtime/translations"
                f"?model={quote(self.model, safe='')}"
            )
        elif self.model == _REALTIME_21_MODEL:
            url = f"wss://api.openai.com/v1/realtime?model={quote(self.model, safe='')}"
        else:
            url = "wss://api.openai.com/v1/realtime"
        return ConnectInfo(url=url, headers=headers)

    def initial_messages(self) -> list[str | bytes]:
        if self.model == _TRANSLATE_MODEL:
            return [
                json.dumps(
                    {
                        "type": "session.update",
                        "session": {
                            "audio": {"output": {"language": self.target_language}},
                        },
                    }
                )
            ]
        if self.model == _REALTIME_21_MODEL:
            transcription: dict[str, object] = {"model": _WHISPER_MODEL}
            if self.language:
                transcription["language"] = self.language
            return [
                json.dumps(
                    {
                        "type": "session.update",
                        "session": {
                            "type": "realtime",
                            "model": self.model,
                            "output_modalities": ["audio"],
                            "audio": {
                                "input": {
                                    "format": {"type": "audio/pcm", "rate": _TARGET_RATE},
                                    "transcription": transcription,
                                    "turn_detection": {"type": "semantic_vad"},
                                },
                                "output": {
                                    "format": {"type": "audio/pcm"},
                                    "voice": "marin",
                                },
                            },
                            "instructions": (
                                "You are a live caption assistant. Prefer concise spoken replies. "
                                "Input audio is transcribed for on-screen captions."
                            ),
                        },
                    }
                )
            ]
        transcription = {"model": self.model}
        if self.language:
            transcription["language"] = self.language
        if self.delay:
            transcription["delay"] = self.delay
        return [
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "type": "transcription",
                        "audio": {
                            "input": {
                                "format": {"type": "audio/pcm", "rate": _TARGET_RATE},
                                "transcription": transcription,
                                "turn_detection": None,
                            }
                        },
                    },
                }
            )
        ]

    def encode_audio(self, pcm16: bytes) -> str | bytes:
        audio = pcm16_to_float32(pcm16)
        resampled = resample_linear(audio, source_rate=self.sample_rate, target_rate=_TARGET_RATE)
        upsampled = float32_to_pcm16(np.asarray(resampled, dtype=np.float32))
        b64 = base64.b64encode(upsampled).decode("ascii")
        event_type = (
            "session.input_audio_buffer.append"
            if self.model == _TRANSLATE_MODEL
            else "input_audio_buffer.append"
        )
        return json.dumps({"type": event_type, "audio": b64})

    async def flush(self) -> None:
        if self.model == _WHISPER_MODEL:
            await self._send(json.dumps({"type": "input_audio_buffer.commit"}))

    async def stop_stream(self) -> None:
        if self.model == _TRANSLATE_MODEL and self._running and self._ws is not None:
            try:
                await self._send(json.dumps({"type": "session.close"}))
            except Exception:
                pass
        await super().stop_stream()

    def parse_event(self, message: str | bytes) -> ParsedEvent:
        try:
            data = json.loads(message)
        except (ValueError, TypeError):
            return ParsedEvent(kind=None)
        event_type = data.get("type")
        if event_type in {_DELTA_EVENT, _OUTPUT_TRANSCRIPT_DELTA, _TRANSLATE_OUTPUT_DELTA}:
            return ParsedEvent(kind="partial", text=str(data.get("delta", "")), is_delta=True)
        if event_type in {_COMPLETED_EVENT, _OUTPUT_TRANSCRIPT_DONE}:
            text = data.get("transcript")
            if text is None:
                text = data.get("delta", "")
            return ParsedEvent(kind="final", text=str(text))
        return ParsedEvent(kind=None)
