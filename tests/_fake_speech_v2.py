"""In-memory fake for google-cloud-speech v2 ``SpeechAsyncClient``.

Mirrors the slice of the gRPC bidi-streaming API the :class:`SpeechV2Backend`
uses: ``await client.streaming_recognize(requests=<async iterator>)`` returns an
async iterator of response objects. The fake drains the backend's request
generator (recording the config-first request and every audio request) and
yields scripted responses, so the streaming logic is exercised with zero
network and without google-cloud-speech installed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class FakeAlternative:
    transcript: str


@dataclass
class FakeResult:
    is_final: bool
    alternatives: list[FakeAlternative]


@dataclass
class FakeResponse:
    results: list[FakeResult]


def make_response(transcript: str, *, is_final: bool) -> FakeResponse:
    return FakeResponse(
        results=[FakeResult(is_final=is_final, alternatives=[FakeAlternative(transcript)])]
    )


# Sentinel placed in a call's response script: when the fake stream reaches it,
# the response iterator ends ON ITS OWN (server-closed the bidi stream) without
# waiting for the request generator to finish, mirroring a natural stream end.
CLOSE_STREAM = object()


@dataclass
class FakeStreamCall:
    """One ``streaming_recognize`` invocation: its captured requests + script."""

    requests: list[object] = field(default_factory=list)
    responses: list[FakeResponse] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)


class _FakeTransport:
    """Stand-in for ``SpeechAsyncClient.transport``.

    ``close()`` is async (mirroring the real grpc_asyncio transport) and shuts
    the channel by flipping the owning client's ``closed`` flag. Once closed, a
    later ``streaming_recognize`` on the same client RAISES -- exactly like a
    real shut-down gRPC channel -- so reusing a closed client is no longer silent.
    """

    def __init__(self, client: FakeSpeechAsyncClient) -> None:
        self._client = client

    async def close(self) -> None:
        self._client.closed = True


class FakeSpeechAsyncClient:
    """Records every streaming call and replays a per-call response script.

    ``response_script`` is a list of per-call response lists. Call *n* consumes
    ``response_script[n]`` (or an empty script when exhausted, e.g. after a
    proactive restart). Each call drains the supplied request async iterator on
    a background task so the backend's config-first + audio requests are
    captured exactly as the real bidi stream would consume them.
    """

    def __init__(
        self,
        response_script: list[list[FakeResponse]] | None = None,
        *,
        always_close: bool = False,
    ) -> None:
        self._response_script = list(response_script or [])
        # When set, EVERY call's response iterator ends immediately after its
        # scripted responses (instead of staying open until the request
        # generator finishes), modelling a server that keeps dropping the bidi
        # stream so the reopen/backoff path can be exercised repeatedly.
        self.always_close = always_close
        self.calls: list[FakeStreamCall] = []
        self.closed = False
        self.transport = _FakeTransport(self)

    async def streaming_recognize(
        self, *, requests: AsyncIterator[object]
    ) -> AsyncIterator[object]:
        if self.closed:
            # A closed gRPC channel cannot open a new bidi stream. The real
            # SpeechAsyncClient errors here; the old fake silently accepted reuse
            # of a closed client, masking the _close_client lifecycle bug.
            raise RuntimeError(
                "streaming_recognize called on a closed transport "
                "(the gRPC channel was shut down by transport.close())"
            )
        index = len(self.calls)
        script = self._response_script[index] if index < len(self._response_script) else []
        call = FakeStreamCall(responses=list(script))
        self.calls.append(call)

        async def _drain() -> None:
            try:
                async for request in requests:
                    call.requests.append(request)
            finally:
                call.done.set()

        drain_task = asyncio.create_task(_drain())

        async def _iter() -> AsyncIterator[object]:
            try:
                for response in call.responses:
                    if response is CLOSE_STREAM:
                        # Server closed the stream on its own: end the response
                        # iterator now (do NOT wait for the request generator).
                        return
                    # Yield to the loop so the request generator can interleave;
                    # this is what surfaces issue #12136 if the generator is sync.
                    await asyncio.sleep(0)
                    yield response
                if self.always_close:
                    # Server keeps dropping the stream: end now so the backend
                    # must decide whether to reopen (and throttle the reopen).
                    return
                # Keep the response stream open until the backend stops feeding
                # requests (sentinel/cancel), mirroring a real open bidi stream.
                await call.done.wait()
            finally:
                drain_task.cancel()

        return _iter()


class _AudioEncoding:
    LINEAR16 = "LINEAR16"


class FakeExplicitDecodingConfig:
    AudioEncoding = _AudioEncoding

    def __init__(self, *, encoding=None, sample_rate_hertz=None, audio_channel_count=None) -> None:
        self.encoding = encoding
        self.sample_rate_hertz = sample_rate_hertz
        self.audio_channel_count = audio_channel_count


class FakeRecognitionConfig:
    def __init__(self, *, explicit_decoding_config=None, language_codes=None, model=None) -> None:
        self.explicit_decoding_config = explicit_decoding_config
        self.language_codes = list(language_codes or [])
        self.model = model


class FakeStreamingRecognitionFeatures:
    def __init__(self, *, interim_results=False) -> None:
        self.interim_results = interim_results


class FakeStreamingRecognitionConfig:
    def __init__(self, *, config=None, streaming_features=None) -> None:
        self.config = config
        self.streaming_features = streaming_features


class FakeStreamingRecognizeRequest:
    def __init__(self, *, recognizer="", streaming_config=None, audio=b"") -> None:
        self.recognizer = recognizer
        self.streaming_config = streaming_config
        self.audio = audio


class FakeCloudSpeechTypes:
    """Duck-typed stand-in for ``google.cloud.speech_v2.types.cloud_speech``."""

    ExplicitDecodingConfig = FakeExplicitDecodingConfig
    RecognitionConfig = FakeRecognitionConfig
    StreamingRecognitionFeatures = FakeStreamingRecognitionFeatures
    StreamingRecognitionConfig = FakeStreamingRecognitionConfig
    StreamingRecognizeRequest = FakeStreamingRecognizeRequest


def config_request(call: FakeStreamCall) -> object:
    """First captured request (config-only)."""
    assert call.requests, "no requests captured for call"
    return call.requests[0]


def audio_payloads(call: FakeStreamCall) -> list[bytes]:
    """Audio bytes from every audio-bearing captured request, in order."""
    payloads: list[bytes] = []
    for request in call.requests:
        audio = getattr(request, "audio", b"")
        if audio:
            payloads.append(audio)
    return payloads
