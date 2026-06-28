from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Callable
from typing import Any

from obs_captions.stt.base import STTBackend, Transcript

# Google Speech-to-Text v2 streaming limits / defaults.
# Each streaming audio request must stay under 25 KB; ~800 ms of 16 kHz mono
# PCM16 (16000 * 2 bytes/s * 0.8 ≈ 25.6 KB), so 25600 is the per-request cap.
_MAX_AUDIO_BYTES = 25600
# A single bidi stream is capped at ~5 min server-side; restart proactively
# well before that so no audio is dropped at the boundary.
_RESTART_INTERVAL_S = 270.0
# Backoff before reopening after an UNPLANNED stream end (server-closed or
# dropped) so a stream that ends immediately+repeatedly can't busy-spin. A
# planned restart (the timer) reopens with no backoff to avoid dropping audio.
_REOPEN_BACKOFF_S = 0.5
_DEFAULT_MODEL = "chirp_2"
# chirp models require a regional endpoint; "global" is INVALID for chirp.
_DEFAULT_LOCATION = "us-central1"

_IMPORT_HINT = (
    "google-cloud-speech is required for google speech_v2 mode. "
    "Install it with `uv sync --extra google`."
)

# Sentinel pushed onto the audio queue to terminate the current request
# generator cleanly (stop) or to roll over to a fresh stream (restart).
_STOP = object()
_RESTART = object()


def _normalize_language(language: str) -> str:
    """Map the project's default ``"ko"`` to the BCP-47 code Speech v2 expects.

    Only the bare default ``"ko"`` is region-qualified to ``"ko-KR"``. Every
    other value passes through UNCHANGED: already-qualified codes such as
    ``"en-US"`` are used verbatim, and any other bare code (e.g. ``"en"``) is the
    caller's responsibility -- they must supply a full locale themselves.
    """
    return "ko-KR" if language == "ko" else language


class SpeechV2Backend(STTBackend):
    """Google Cloud Speech-to-Text v2 streaming backend (chirp_2, Korean default).

    Bridges the project's push-style ``feed_audio(pcm16)`` to google-cloud-speech's
    gRPC bidirectional streaming. Audio is buffered onto an ``asyncio.Queue`` and
    drained by an ``async def`` request generator that is consumed concurrently
    with the response iterator (a sync generator deadlocks interim results, see
    google-cloud-python issue #12136). The first request carries config only;
    subsequent requests carry audio (split to <=25 KB each). A background drive
    task proactively restarts the stream before the ~5 min server limit, reusing
    audio still buffered on the queue.

    Interim results (``on_partial``) carry the FULL rolling hypothesis (not a
    delta); finals (``on_final``) carry committed segments. The Speech client and
    the ``cloud_speech`` types module are injectable so tests run with a fake and
    never touch the network or require google-cloud-speech to be installed.
    """

    def __init__(
        self,
        *,
        language: str = "ko",
        sample_rate: int = 16000,
        on_partial: Callable[[Transcript], None],
        on_final: Callable[[Transcript], None],
        model: str = _DEFAULT_MODEL,
        location: str = _DEFAULT_LOCATION,
        project_id: str | None = None,
        client: Any | None = None,
        types: Any | None = None,
        restart_interval_s: float = _RESTART_INTERVAL_S,
        reopen_backoff_s: float = _REOPEN_BACKOFF_S,
        max_audio_bytes: int = _MAX_AUDIO_BYTES,
    ) -> None:
        super().__init__(
            language=language,
            sample_rate=sample_rate,
            on_partial=on_partial,
            on_final=on_final,
        )
        self.model = model
        self.location = location
        # chirp models are served only from regional endpoints; "global" (and an
        # empty location) would build an invalid recognizer path + endpoint, so
        # fail fast at construction rather than at the first gRPC call.
        if self.model.startswith("chirp") and self.location in {"", "global"}:
            raise ValueError(
                f"location={self.location!r} is invalid for chirp models "
                f"({self.model}): chirp requires a regional endpoint such as "
                "'us-central1', not 'global'."
            )
        self.project_id = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT") or ""
        if not self.project_id:
            raise ValueError(
                "GOOGLE_CLOUD_PROJECT must be set (or pass project_id=) for google speech_v2 mode."
            )
        self._language_code = _normalize_language(language)
        self._client = client
        # The backend owns (and rebuilds) only an auto-built client. A client
        # injected by the caller is the caller's to close/reuse -- see
        # _close_client. Mirrors the ownership flag in replicate/openrouter.
        self._owns_client = client is None
        self._types = types
        self._restart_interval_s = restart_interval_s
        self._reopen_backoff_s = max(0.0, reopen_backoff_s)
        self._max_audio_bytes = max(1, max_audio_bytes)
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._drive_task: asyncio.Task[None] | None = None
        self._running = False

    # -------------------------------------------------------------- lifecycle
    async def start_stream(self) -> None:
        if self._running:
            return
        if self._client is None:
            self._client = self._build_client()
        if self._types is None:
            self._types = self._load_types()
        # Start every session on a FRESH queue. A reused queue can still hold a
        # stale _STOP (and any audio) from the previous session; that sentinel
        # would terminate the new stream immediately and spin the drive loop into
        # an endless throttled reopen. A new queue makes the _STOP from a prior
        # stop_stream() belong to that stream's lifecycle alone.
        self._queue = asyncio.Queue()
        self._running = True
        self._drive_task = asyncio.create_task(self._drive())

    async def feed_audio(self, pcm16: bytes) -> None:
        if not self._running or not pcm16:
            return
        for start in range(0, len(pcm16), self._max_audio_bytes):
            self._queue.put_nowait(pcm16[start : start + self._max_audio_bytes])

    async def flush(self) -> None:
        """Speech v2 finalizes server-side; nothing buffered to flush locally."""
        return None

    async def stop_stream(self) -> None:
        if not self._running:
            return
        self._running = False
        self._queue.put_nowait(_STOP)
        task = self._drive_task
        self._drive_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self._close_client()

    # ------------------------------------------------------------- internals
    async def _drive(self) -> None:
        """Run streams back-to-back, reopening until stop is requested.

        Each iteration opens one bidi stream and consumes it until the restart
        timer fires, the stream ends on its own (server-closed / dropped), or
        stop is requested. While ``_running`` holds, ANY end other than stop
        reopens a fresh stream, so audio queued afterwards is never silently
        dropped; audio still buffered on the shared queue carries over. A planned
        restart reopens immediately (lose no audio at the boundary); an unplanned
        end backs off briefly first so a stream that ends immediately+repeatedly
        cannot busy-spin.
        """
        while self._running:
            planned_restart = await self._run_one_stream()
            if not self._running:
                return
            if not planned_restart:
                await asyncio.sleep(self._reopen_backoff_s)

    async def _run_one_stream(self) -> bool:
        """Open + consume one stream.

        Returns True only when the planned restart timer fired (``_drive``
        reopens with no backoff). A natural end (the response iterator exhausts
        because the server closed the stream) or a dropped stream returns False;
        ``_drive`` then reopens with a short backoff while ``_running`` holds, so
        subsequently queued audio reaches a fresh stream instead of being lost.
        """
        restart_flag = {"value": False}

        async def _requests() -> AsyncIterator[Any]:
            yield self._build_config_request()
            while True:
                item = await self._queue.get()
                if item is _STOP:
                    # _STOP belongs to exactly one stream's lifecycle: stop_stream
                    # set _running=False before enqueuing it, so the _running-gated
                    # _drive loop will not reopen. Do NOT re-enqueue it -- a leftover
                    # sentinel would poison the next stream on the same queue.
                    return
                if item is _RESTART:
                    restart_flag["value"] = True
                    return
                yield self._build_audio_request(item)

        timer = asyncio.create_task(self._restart_timer())
        try:
            response_iter = await self._client.streaming_recognize(requests=_requests())
            async for response in response_iter:
                self._handle_response(response)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A dropped stream falls through; _drive reopens (with backoff) while
            # ``_running`` holds, preserving queued audio across the reopen.
            pass
        finally:
            timer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await timer
        return restart_flag["value"]

    async def _restart_timer(self) -> None:
        await asyncio.sleep(self._restart_interval_s)
        if self._running:
            self._queue.put_nowait(_RESTART)

    def _handle_response(self, response: Any) -> None:
        for result in getattr(response, "results", []) or []:
            alternatives = getattr(result, "alternatives", None) or []
            if not alternatives:
                continue
            text = getattr(alternatives[0], "transcript", "") or ""
            if not text:
                continue
            if getattr(result, "is_final", False):
                self.on_final(Transcript(text=text, is_final=True, lang=self.language))
            else:
                self.on_partial(Transcript(text=text, is_final=False, lang=self.language))

    # --------------------------------------------------------- request builders
    def _build_config_request(self) -> Any:
        types = self._types
        decoding = types.ExplicitDecodingConfig(
            encoding=types.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=self.sample_rate,
            audio_channel_count=1,
        )
        config = types.RecognitionConfig(
            explicit_decoding_config=decoding,
            language_codes=[self._language_code],
            model=self.model,
        )
        streaming_config = types.StreamingRecognitionConfig(
            config=config,
            streaming_features=types.StreamingRecognitionFeatures(interim_results=True),
        )
        recognizer = f"projects/{self.project_id}/locations/{self.location}/recognizers/_"
        return types.StreamingRecognizeRequest(
            recognizer=recognizer,
            streaming_config=streaming_config,
        )

    def _build_audio_request(self, pcm16: bytes) -> Any:
        return self._types.StreamingRecognizeRequest(audio=pcm16)

    # --------------------------------------------------------- lazy GCP imports
    def _build_client(self) -> Any:
        try:
            from google.api_core.client_options import ClientOptions
            from google.cloud.speech_v2 import SpeechAsyncClient
        except ImportError as exc:  # pragma: no cover - exercised via fake injection
            raise ImportError(_IMPORT_HINT) from exc
        options = ClientOptions(api_endpoint=f"{self.location}-speech.googleapis.com")
        return SpeechAsyncClient(client_options=options)

    def _load_types(self) -> Any:
        try:
            from google.cloud.speech_v2.types import cloud_speech
        except ImportError as exc:  # pragma: no cover - exercised via fake injection
            raise ImportError(_IMPORT_HINT) from exc
        return cloud_speech

    async def _close_client(self) -> None:
        # Only tear down a client we OWN (auto-built because client was None). An
        # injected client belongs to the caller -- leave it untouched so the
        # caller controls its lifecycle and can reuse it across sessions.
        if not self._owns_client:
            return
        client = self._client
        if client is None:
            return
        transport = getattr(client, "transport", None)
        close = getattr(transport, "close", None)
        if close is not None:
            with contextlib.suppress(Exception):
                result = close()
                if asyncio.iscoroutine(result):
                    await result
        # Drop the closed client so the next start_stream() rebuilds a fresh one:
        # a closed gRPC channel cannot serve a new streaming_recognize. Without
        # this, start_stream's ``self._client is None`` guard reuses the dead
        # client and every stream fails on the real google-cloud-speech path.
        self._client = None
