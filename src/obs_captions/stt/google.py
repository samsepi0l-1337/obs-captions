from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable

from obs_captions.stt.base import STTBackend, Transcript
from obs_captions.stt.streaming import ConnectInfo, ParsedEvent, StreamingBackend

_HOST = "generativelanguage.googleapis.com"
_PATH = "/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
_DEFAULT_MODEL = "gemini-3.1-flash-live-preview"
_MIME = "audio/pcm;rate=16000"


def build_google_backend(
    *,
    mode: str = "gemini",
    on_partial: Callable[[Transcript], None],
    on_final: Callable[[Transcript], None],
    **kwargs: object,
) -> STTBackend:
    """Construct the google STT backend for the requested mode.

    ``mode='gemini'`` → Gemini Live websocket (:class:`GoogleBackend`).
    ``mode='speech_v2'`` → Speech-to-Text v2 streaming
    (:class:`~obs_captions.stt.google_speech_v2.SpeechV2Backend`, chirp_2).
    Unknown modes raise ``ValueError``.
    """
    if mode == "speech_v2":
        from obs_captions.stt.google_speech_v2 import SpeechV2Backend

        return SpeechV2Backend(on_partial=on_partial, on_final=on_final, **kwargs)  # type: ignore[arg-type]
    if mode != "gemini":
        raise ValueError(f"Unknown google mode: '{mode}' (use 'gemini' or 'speech_v2').")
    return GoogleBackend(mode=mode, on_partial=on_partial, on_final=on_final, **kwargs)  # type: ignore[arg-type]


class GoogleBackend(StreamingBackend):
    """Google streaming STT. Default mode: Gemini Live API over websocket.

    Doc source: ai.google.dev/gemini-api/docs/live-api/get-started-websocket.
    URL ``wss://generativelanguage.googleapis.com/ws/...BidiGenerateContent?key=``;
    ``setup`` frame selects the model + enables ``inputAudioTranscription``;
    audio sent via ``realtimeInput.audio`` (base64 16 kHz PCM16,
    ``audio/pcm;rate=16000``). Transcription returns as
    ``serverContent.inputTranscription.text`` deltas (accumulated → on_partial)
    and on turn completion (``serverContent.turnComplete``) → on_final.

    Audio-only Live sessions are capped at ~15 min; the base reconnect loop
    re-opens the socket automatically when the server closes the session.

    For ``mode='speech_v2'`` use :func:`build_google_backend`, which dispatches
    to :class:`~obs_captions.stt.google_speech_v2.SpeechV2Backend`.
    """

    def __init__(
        self,
        *,
        mode: str = "gemini",
        model: str = _DEFAULT_MODEL,
        api_key: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.mode = mode
        self.model = model
        if mode != "gemini":
            raise ValueError(f"Unknown google mode: '{mode}' (use 'gemini' or 'speech_v2').")
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY") or ""
        if not self._api_key:
            raise ValueError(
                "GEMINI_API_KEY is required for GoogleBackend (gemini mode). "
                "Set it in .env or pass api_key=."
            )

    def build_connect(self) -> ConnectInfo:
        url = f"wss://{_HOST}{_PATH}?key={self._api_key}"
        return ConnectInfo(url=url, headers={})

    def initial_messages(self) -> list[str | bytes]:
        setup: dict[str, object] = {
            "model": f"models/{self.model}",
            "generationConfig": {"responseModalities": ["TEXT"]},
            "inputAudioTranscription": {},
        }
        return [json.dumps({"setup": setup})]

    def encode_audio(self, pcm16: bytes) -> str | bytes:
        b64 = base64.b64encode(pcm16).decode("ascii")
        return json.dumps({"realtimeInput": {"audio": {"data": b64, "mimeType": _MIME}}})

    def parse_event(self, message: str | bytes) -> ParsedEvent:
        try:
            data = json.loads(message)
        except (ValueError, TypeError):
            return ParsedEvent(kind=None)
        server_content = data.get("serverContent")
        if not isinstance(server_content, dict):
            return ParsedEvent(kind=None)
        transcription = server_content.get("inputTranscription")
        if isinstance(transcription, dict):
            text = str(transcription.get("text", ""))
            if text:
                return ParsedEvent(kind="partial", text=text, is_delta=True)
        if server_content.get("turnComplete"):
            return ParsedEvent(kind="final", text=self._partial_accum)
        return ParsedEvent(kind=None)
