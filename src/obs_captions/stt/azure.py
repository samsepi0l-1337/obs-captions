"""Azure Cognitive Services Speech SDK real-time STT backend.

Integration approach: SDK (Option A).
Uses ``PushAudioInputStream`` to feed PCM16 audio and ``SpeechRecognizer``
with continuous recognition for streaming partial + final transcripts.

The Azure Speech SDK fires ``recognizing`` (partial hypothesis) and
``recognized`` (final committed segment) callbacks on SDK background threads.
These are bridged to the asyncio event loop via ``loop.call_soon_threadsafe``
so the app's on_partial / on_final callables are always invoked on the loop
thread — thread-safe and consistent with every other backend.

Required configuration (distinct from single-key providers):
  AZURE_SPEECH_KEY   — subscription key
  AZURE_SPEECH_REGION — service region, e.g. ``eastus``

Both can also be passed as constructor arguments.  The ``speechsdk`` argument
accepts an injectable namespace so unit tests run with a fake without the real
SDK or any network access.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Callable
from typing import Any

from obs_captions.stt.base import STTBackend, Transcript

_IMPORT_HINT = (
    "azure-cognitiveservices-speech is required for azure mode. "
    "Install it with `uv sync --extra azure`."
)


def _normalize_language(language: str) -> str:
    """Map bare ``"ko"`` to ``"ko-KR"``; every other value passes through unchanged."""
    return "ko-KR" if language == "ko" else language


class AzureBackend(STTBackend):
    """Azure Cognitive Services Speech SDK real-time STT backend (Korean default).

    Bridges the project's push-style ``feed_audio(pcm16)`` to the Azure Speech
    SDK's ``PushAudioInputStream``.  ``recognizing`` events (partial hypotheses)
    map to ``on_partial``; ``recognized`` events (committed segments) map to
    ``on_final``.  SDK callbacks fire on background threads and are forwarded to
    the asyncio event loop thread via ``call_soon_threadsafe`` — no threading
    hazard for callers.

    The ``speechsdk`` argument is injectable so tests can pass a fake module and
    never touch the network or require ``azure-cognitiveservices-speech``.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        region: str | None = None,
        language: str = "ko",
        sample_rate: int = 16000,
        on_partial: Callable[[Transcript], None],
        on_final: Callable[[Transcript], None],
        speechsdk: Any | None = None,
    ) -> None:
        super().__init__(
            language=language,
            sample_rate=sample_rate,
            on_partial=on_partial,
            on_final=on_final,
        )
        self._api_key = api_key or os.environ.get("AZURE_SPEECH_KEY") or ""
        self._region = region or os.environ.get("AZURE_SPEECH_REGION") or ""
        if not self._api_key:
            raise ValueError(
                "AZURE_SPEECH_KEY is required for AzureBackend. "
                "Set it in .env or pass api_key=."
            )
        if not self._region:
            raise ValueError(
                "AZURE_SPEECH_REGION is required for AzureBackend. "
                "Set it in .env or pass region=."
            )
        self._language_code = _normalize_language(language)
        self._speechsdk = speechsdk  # None → lazy import on first start_stream
        self._push_stream: Any | None = None
        self._recognizer: Any | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None

    # -------------------------------------------------------------- lifecycle

    async def start_stream(self) -> None:
        if self._running:
            return
        sdk = self._speechsdk or self._load_sdk()
        loop = asyncio.get_running_loop()
        self._loop = loop

        speech_config = sdk.SpeechConfig(subscription=self._api_key, region=self._region)
        speech_config.speech_recognition_language = self._language_code

        push_stream = sdk.audio.PushAudioInputStream()
        audio_config = sdk.audio.AudioConfig(stream=push_stream)
        recognizer = sdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

        def _on_recognizing(evt: Any) -> None:
            text = getattr(getattr(evt, "result", None), "text", "") or ""
            if text:
                loop.call_soon_threadsafe(
                    self.on_partial,
                    Transcript(text=text, is_final=False, lang=self.language),
                )

        def _on_recognized(evt: Any) -> None:
            text = getattr(getattr(evt, "result", None), "text", "") or ""
            if text:
                loop.call_soon_threadsafe(
                    self.on_final,
                    Transcript(text=text, is_final=True, lang=self.language),
                )

        recognizer.recognizing.connect(_on_recognizing)
        recognizer.recognized.connect(_on_recognized)

        self._push_stream = push_stream
        self._recognizer = recognizer
        self._running = True

        # start_continuous_recognition_async() is non-blocking; .get() blocks
        # until the recognition session is ready.  Run in executor so the event
        # loop remains responsive during the brief SDK handshake.
        await loop.run_in_executor(None, recognizer.start_continuous_recognition_async().get)

    async def feed_audio(self, pcm16: bytes) -> None:
        if not self._running or not pcm16 or self._push_stream is None:
            return
        # PushAudioInputStream.write() is a fast buffer-copy; safe to call on
        # the event loop thread (does not block meaningfully).
        self._push_stream.write(pcm16)

    async def flush(self) -> None:
        """Azure finalizes server-side; nothing buffered locally to flush."""
        return None

    async def stop_stream(self) -> None:
        if not self._running:
            return
        self._running = False
        recognizer = self._recognizer
        push_stream = self._push_stream
        self._recognizer = None
        self._push_stream = None
        loop = self._loop or asyncio.get_running_loop()
        if recognizer is not None:
            with contextlib.suppress(Exception):
                await loop.run_in_executor(
                    None, recognizer.stop_continuous_recognition_async().get
                )
        if push_stream is not None:
            with contextlib.suppress(Exception):
                push_stream.close()

    # --------------------------------------------------------- lazy SDK import

    def _load_sdk(self) -> Any:
        try:
            import azure.cognitiveservices.speech as speechsdk  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - requires azure-cognitiveservices-speech absent at runtime; tests inject fake SDK to bypass this import
            raise ImportError(_IMPORT_HINT) from exc
        return speechsdk
