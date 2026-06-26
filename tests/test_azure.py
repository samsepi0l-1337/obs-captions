"""Unit tests for AzureBackend using a fully-injected fake SDK.

No network, no real azure-cognitiveservices-speech required.  The fake SDK
mirrors the narrow slice of the Azure Speech SDK surface that AzureBackend
uses, so every code path is exercised in-process.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from obs_captions.stt.base import Transcript


# ------------------------------------------------------------------ fake SDK


@dataclass
class FakePushAudioInputStream:
    written: list[bytes] = field(default_factory=list)
    closed: bool = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeAudioConfig:
    stream: object


@dataclass
class FakeSpeechConfig:
    subscription: str
    region: str
    speech_recognition_language: str = ""


@dataclass
class FakeResult:
    text: str


@dataclass
class FakeEvent:
    result: FakeResult

    @classmethod
    def make(cls, text: str) -> "FakeEvent":
        return cls(result=FakeResult(text=text))


class FakeEventHandler:
    def __init__(self) -> None:
        self._callbacks: list = []

    def connect(self, cb) -> None:  # noqa: ANN001
        self._callbacks.append(cb)

    def fire(self, evt: object) -> None:
        for cb in self._callbacks:
            cb(evt)


class FakeResultFuture:
    def get(self) -> None:
        pass


class FakeSpeechRecognizer:
    def __init__(self, *, speech_config: FakeSpeechConfig, audio_config: FakeAudioConfig) -> None:
        self.speech_config = speech_config
        self.audio_config = audio_config
        self.recognizing: FakeEventHandler = FakeEventHandler()
        self.recognized: FakeEventHandler = FakeEventHandler()
        self.started: bool = False
        self.stopped: bool = False

    def start_continuous_recognition_async(self) -> FakeResultFuture:
        self.started = True
        return FakeResultFuture()

    def stop_continuous_recognition_async(self) -> FakeResultFuture:
        self.stopped = True
        return FakeResultFuture()


class _FakeAudio:
    def __init__(self) -> None:
        self.last_push_stream: FakePushAudioInputStream | None = None

    def PushAudioInputStream(self) -> FakePushAudioInputStream:  # noqa: N802
        s = FakePushAudioInputStream()
        self.last_push_stream = s
        return s

    def AudioConfig(self, *, stream: object) -> FakeAudioConfig:  # noqa: N802
        return FakeAudioConfig(stream=stream)


class FakeSpeechSdk:
    def __init__(self) -> None:
        self.audio = _FakeAudio()
        self.last_speech_config: FakeSpeechConfig | None = None
        self.last_recognizer: FakeSpeechRecognizer | None = None

    def SpeechConfig(self, *, subscription: str, region: str) -> FakeSpeechConfig:  # noqa: N802
        cfg = FakeSpeechConfig(subscription=subscription, region=region)
        self.last_speech_config = cfg
        return cfg

    def SpeechRecognizer(  # noqa: N802
        self,
        *,
        speech_config: FakeSpeechConfig,
        audio_config: FakeAudioConfig,
    ) -> FakeSpeechRecognizer:
        r = FakeSpeechRecognizer(speech_config=speech_config, audio_config=audio_config)
        self.last_recognizer = r
        return r


# ------------------------------------------------------------------ helpers


def _make(
    sdk: FakeSpeechSdk,
    partials: list[Transcript],
    finals: list[Transcript],
    **overrides: object,
):
    from obs_captions.stt.azure import AzureBackend

    kwargs: dict[str, object] = dict(
        api_key="test-key",
        region="eastus",
        language="ko",
        on_partial=partials.append,
        on_final=finals.append,
        speechsdk=sdk,
    )
    kwargs.update(overrides)
    return AzureBackend(**kwargs)


# ------------------------------------------------------------------ tests


def test_normalize_language_only_maps_ko() -> None:
    from obs_captions.stt.azure import _normalize_language

    assert _normalize_language("ko") == "ko-KR"
    assert _normalize_language("en-US") == "en-US"
    assert _normalize_language("en") == "en"


@pytest.mark.asyncio
async def test_speech_config_carries_key_region_and_ko_kr() -> None:
    sdk = FakeSpeechSdk()
    backend = _make(sdk, [], [], language="ko")
    await backend.start_stream()
    try:
        cfg = sdk.last_speech_config
        assert cfg is not None
        assert cfg.subscription == "test-key"
        assert cfg.region == "eastus"
        assert cfg.speech_recognition_language == "ko-KR"
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_language_passthrough_for_non_ko() -> None:
    sdk = FakeSpeechSdk()
    backend = _make(sdk, [], [], language="en-US")
    await backend.start_stream()
    try:
        assert sdk.last_speech_config is not None
        assert sdk.last_speech_config.speech_recognition_language == "en-US"
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_feed_audio_writes_pcm16_to_push_stream() -> None:
    sdk = FakeSpeechSdk()
    backend = _make(sdk, [], [])
    await backend.start_stream()
    try:
        pcm = b"\x01\x02" * 100
        await backend.feed_audio(pcm)
        assert sdk.audio.last_push_stream is not None
        assert b"".join(sdk.audio.last_push_stream.written) == pcm
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_recognizing_event_emits_on_partial_with_full_hypothesis() -> None:
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    sdk = FakeSpeechSdk()
    backend = _make(sdk, partials, finals)
    await backend.start_stream()
    try:
        assert sdk.last_recognizer is not None
        sdk.last_recognizer.recognizing.fire(FakeEvent.make("안녕하세요"))
        await asyncio.sleep(0)  # drain call_soon_threadsafe
        assert len(partials) == 1
        assert partials[0].text == "안녕하세요"
        assert partials[0].is_final is False
        assert partials[0].lang == "ko"
        assert finals == []
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_recognized_event_emits_on_final() -> None:
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    sdk = FakeSpeechSdk()
    backend = _make(sdk, partials, finals)
    await backend.start_stream()
    try:
        assert sdk.last_recognizer is not None
        sdk.last_recognizer.recognized.fire(FakeEvent.make("안녕하세요"))
        await asyncio.sleep(0)
        assert len(finals) == 1
        assert finals[0].text == "안녕하세요"
        assert finals[0].is_final is True
        assert finals[0].lang == "ko"
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_empty_text_from_recognizing_is_ignored() -> None:
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    sdk = FakeSpeechSdk()
    backend = _make(sdk, partials, finals)
    await backend.start_stream()
    try:
        assert sdk.last_recognizer is not None
        sdk.last_recognizer.recognizing.fire(FakeEvent.make(""))
        sdk.last_recognizer.recognized.fire(FakeEvent.make(""))
        await asyncio.sleep(0)
        assert partials == []
        assert finals == []
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_start_stop_lifecycle_recognizer_started_once_stopped_cleanly() -> None:
    sdk = FakeSpeechSdk()
    backend = _make(sdk, [], [])
    await backend.start_stream()
    recognizer = sdk.last_recognizer
    assert recognizer is not None
    assert recognizer.started is True
    assert recognizer.stopped is False
    await backend.stop_stream()
    assert recognizer.stopped is True
    # Push stream must be closed on stop (no resource leak).
    assert sdk.audio.last_push_stream is not None
    assert sdk.audio.last_push_stream.closed is True


@pytest.mark.asyncio
async def test_stop_stream_is_idempotent() -> None:
    sdk = FakeSpeechSdk()
    backend = _make(sdk, [], [])
    await backend.start_stream()
    await backend.stop_stream()
    await backend.stop_stream()  # must not raise


@pytest.mark.asyncio
async def test_start_stream_is_idempotent_does_not_recreate_recognizer() -> None:
    sdk = FakeSpeechSdk()
    backend = _make(sdk, [], [])
    await backend.start_stream()
    first = sdk.last_recognizer
    await backend.start_stream()  # second call while running → early return
    assert sdk.last_recognizer is first  # no new recognizer created
    await backend.stop_stream()


@pytest.mark.asyncio
async def test_feed_before_start_is_noop() -> None:
    sdk = FakeSpeechSdk()
    backend = _make(sdk, [], [])
    await backend.feed_audio(b"\x00\x01")  # not started → ignored, no crash
    assert sdk.audio.last_push_stream is None  # PushAudioInputStream never created


def test_missing_api_key_raises() -> None:
    import os

    sdk = FakeSpeechSdk()
    old = os.environ.pop("AZURE_SPEECH_KEY", None)
    try:
        with pytest.raises(ValueError, match="AZURE_SPEECH_KEY"):
            from obs_captions.stt.azure import AzureBackend

            AzureBackend(
                region="eastus",
                on_partial=lambda t: None,
                on_final=lambda t: None,
                speechsdk=sdk,
            )
    finally:
        if old is not None:
            os.environ["AZURE_SPEECH_KEY"] = old


def test_missing_region_raises() -> None:
    import os

    sdk = FakeSpeechSdk()
    old = os.environ.pop("AZURE_SPEECH_REGION", None)
    try:
        with pytest.raises(ValueError, match="AZURE_SPEECH_REGION"):
            from obs_captions.stt.azure import AzureBackend

            AzureBackend(
                api_key="test-key",
                on_partial=lambda t: None,
                on_final=lambda t: None,
                speechsdk=sdk,
            )
    finally:
        if old is not None:
            os.environ["AZURE_SPEECH_REGION"] = old
