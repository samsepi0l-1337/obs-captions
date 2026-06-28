from __future__ import annotations

import asyncio

import pytest

from obs_captions.stt.base import Transcript
from tests._fake_speech_v2 import (
    CLOSE_STREAM,
    FakeCloudSpeechTypes,
    FakeSpeechAsyncClient,
    audio_payloads,
    config_request,
    make_response,
)

_PROJECT = "test-project"


def _make(
    client: FakeSpeechAsyncClient,
    partials: list[Transcript],
    finals: list[Transcript],
    **overrides: object,
):
    from obs_captions.stt.google_speech_v2 import SpeechV2Backend

    kwargs: dict[str, object] = dict(
        language="ko",
        on_partial=partials.append,
        on_final=finals.append,
        project_id=_PROJECT,
        client=client,
        types=FakeCloudSpeechTypes,
    )
    kwargs.update(overrides)
    return SpeechV2Backend(**kwargs)


async def _drain_call(client: FakeSpeechAsyncClient, index: int = 0) -> None:
    """Wait until the backend has opened call ``index`` and captured its config."""
    from tests._fake_ws import wait_for

    await wait_for(lambda: len(client.calls) > index and bool(client.calls[index].requests))


@pytest.mark.asyncio
async def test_first_request_is_config_only_with_korean_chirp2():
    client = FakeSpeechAsyncClient()
    backend = _make(client, [], [])
    await backend.start_stream()
    try:
        await _drain_call(client)
        req = config_request(client.calls[0])
        assert req.recognizer == f"projects/{_PROJECT}/locations/us-central1/recognizers/_"
        assert req.audio in (b"", None)
        cfg = req.streaming_config.config
        assert cfg.language_codes == ["ko-KR"]
        assert cfg.model == "chirp_2"
        dec = cfg.explicit_decoding_config
        assert dec.encoding == FakeCloudSpeechTypes.ExplicitDecodingConfig.AudioEncoding.LINEAR16
        assert dec.sample_rate_hertz == 16000
        assert dec.audio_channel_count == 1
        assert req.streaming_config.streaming_features.interim_results is True
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_language_with_region_passthrough():
    client = FakeSpeechAsyncClient()
    backend = _make(client, [], [], language="en-US")
    await backend.start_stream()
    try:
        await _drain_call(client)
        cfg = config_request(client.calls[0]).streaming_config.config
        assert cfg.language_codes == ["en-US"]
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_feed_audio_emitted_after_config():
    client = FakeSpeechAsyncClient()
    backend = _make(client, [], [])
    await backend.start_stream()
    try:
        await _drain_call(client)
        await backend.feed_audio(b"\x01\x02" * 100)  # 200 bytes
        from tests._fake_ws import wait_for

        await wait_for(lambda: audio_payloads(client.calls[0]) != [])
        payloads = audio_payloads(client.calls[0])
        assert b"".join(payloads) == b"\x01\x02" * 100
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_large_audio_is_split_into_max_25kb_requests():
    client = FakeSpeechAsyncClient()
    backend = _make(client, [], [])
    await backend.start_stream()
    try:
        await _drain_call(client)
        big = b"\x00" * (25600 * 2 + 10)  # > 2 max chunks
        await backend.feed_audio(big)
        from tests._fake_ws import wait_for

        await wait_for(lambda: len(audio_payloads(client.calls[0])) >= 3)
        payloads = audio_payloads(client.calls[0])
        assert all(len(chunk) <= 25600 for chunk in payloads)
        assert b"".join(payloads) == big
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_interim_result_emits_full_partial():
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    client = FakeSpeechAsyncClient([[make_response("안녕하세요", is_final=False)]])
    backend = _make(client, partials, finals)
    await backend.start_stream()
    try:
        from tests._fake_ws import wait_for

        await wait_for(lambda: len(partials) >= 1)
        assert partials[-1].text == "안녕하세요"
        assert partials[-1].is_final is False
        assert partials[-1].lang == "ko"
        assert finals == []
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_final_result_emits_on_final():
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    client = FakeSpeechAsyncClient(
        [[make_response("안녕", is_final=False), make_response("안녕하세요", is_final=True)]]
    )
    backend = _make(client, partials, finals)
    await backend.start_stream()
    try:
        from tests._fake_ws import wait_for

        await wait_for(lambda: len(finals) >= 1)
        assert finals[-1].text == "안녕하세요"
        assert finals[-1].is_final is True
        assert partials and partials[0].text == "안녕"
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_empty_transcript_is_ignored():
    partials: list[Transcript] = []
    finals: list[Transcript] = []
    client = FakeSpeechAsyncClient(
        [[make_response("", is_final=False), make_response("", is_final=True)]]
    )
    backend = _make(client, partials, finals)
    await backend.start_stream()
    try:
        await asyncio.sleep(0.05)
        assert partials == []
        assert finals == []
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_proactive_restart_starts_new_stream_without_losing_audio():
    # Two calls scripted: restart fires after the first, audio fed across it must
    # land on the second stream's requests (re-sent from the buffer).
    client = FakeSpeechAsyncClient([[], []])
    backend = _make(client, [], [], restart_interval_s=0.05)
    await backend.start_stream()
    try:
        from tests._fake_ws import wait_for

        await _drain_call(client, 0)
        await backend.feed_audio(b"\xab\xcd" * 50)
        # Wait for the restart to open a second stream.
        await wait_for(lambda: len(client.calls) >= 2, timeout=2.0)
        await _drain_call(client, 1)
        # Second stream re-emits a config-first request.
        second_cfg = config_request(client.calls[1])
        assert second_cfg.streaming_config is not None
        # Audio fed before the restart is not lost: feed more and confirm flow.
        await backend.feed_audio(b"\x11\x22" * 50)
        await wait_for(lambda: audio_payloads(client.calls[1]) != [], timeout=2.0)
        assert audio_payloads(client.calls[1])
        # Strengthened (finding 4): prove NO audio is lost across the restart.
        # The design delivers pre-restart audio to the live stream (call 0) and
        # does NOT re-send already-delivered audio to the reopened stream, so the
        # precise no-loss guarantee is that the UNION across every stream equals
        # exactly the bytes fed, in FIFO order -- nothing dropped or duplicated.
        fed = b"\xab\xcd" * 50 + b"\x11\x22" * 50

        def _delivered() -> bytes:
            chunks: list[bytes] = []
            for call in client.calls:
                chunks.extend(audio_payloads(call))
            return b"".join(chunks)

        await wait_for(lambda: len(_delivered()) >= len(fed), timeout=2.0)
        assert _delivered() == fed
        # The pre-restart audio was delivered to the live stream (call 0), not
        # re-sent to the reopened one -- documenting the actual restart behavior.
        assert b"\xab\xcd" * 50 in b"".join(audio_payloads(client.calls[0]))
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_stop_stream_terminates_generator_and_cancels_cleanly():
    client = FakeSpeechAsyncClient([[]])
    backend = _make(client, [], [])
    await backend.start_stream()
    await _drain_call(client)
    await backend.stop_stream()
    # The request generator terminated (drain completed via sentinel/cancel).
    from tests._fake_ws import wait_for

    await wait_for(lambda: client.calls[0].done.is_set())
    # Idempotent stop.
    await backend.stop_stream()


@pytest.mark.asyncio
async def test_feed_before_start_is_noop():
    client = FakeSpeechAsyncClient()
    backend = _make(client, [], [])
    await backend.feed_audio(b"\x00\x01")  # not started -> ignored, no crash
    assert client.calls == []


def test_missing_project_id_raises():
    from obs_captions.stt.google_speech_v2 import SpeechV2Backend

    import os

    old = os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
    try:
        with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT"):
            SpeechV2Backend(
                on_partial=lambda t: None,
                on_final=lambda t: None,
                client=FakeSpeechAsyncClient(),
                types=FakeCloudSpeechTypes,
            )
    finally:
        if old is not None:
            os.environ["GOOGLE_CLOUD_PROJECT"] = old


def test_normalize_language_only_maps_ko():
    # Finding 1: only "ko" gains a region; every other bare code is the user's
    # responsibility and must pass through UNCHANGED (no blanket "-KR").
    from obs_captions.stt.google_speech_v2 import _normalize_language

    assert _normalize_language("ko") == "ko-KR"
    assert _normalize_language("en-US") == "en-US"
    assert _normalize_language("en") == "en"


@pytest.mark.parametrize("bad_location", ["global", ""])
def test_non_regional_location_rejected_for_chirp(bad_location):
    # Finding 2 (+ mechanical gap): chirp models have no "global" endpoint, and an
    # empty location is not a regional endpoint either -- BOTH must fail fast at
    # construction with an actionable message, not at the first gRPC call.
    from obs_captions.stt.google_speech_v2 import SpeechV2Backend

    with pytest.raises(ValueError, match="regional"):
        SpeechV2Backend(
            on_partial=lambda t: None,
            on_final=lambda t: None,
            project_id=_PROJECT,
            client=FakeSpeechAsyncClient(),
            types=FakeCloudSpeechTypes,
            location=bad_location,
        )


def test_registry_rejects_explicit_empty_location_for_speech_v2():
    # Bug A: an explicit location="" in TOML must NOT be masked by the registry's
    # truthiness default -- it must reach the SpeechV2Backend ctor guard and raise.
    # With a truthiness filter, "" silently becomes us-central1 and the guard is
    # bypassed; only None/unset may fall back to the regional default.
    from obs_captions.config import AppConfig, ProviderConfig
    from obs_captions.stt.registry import create_backend

    cfg = AppConfig(
        engine="google",
        providers={"google": ProviderConfig(mode="speech_v2", project_id=_PROJECT, location="")},
    )
    with pytest.raises(ValueError, match="regional"):
        create_backend(cfg, on_partial=lambda t: None, on_final=lambda t: None)


def test_registry_speech_v2_unset_location_defaults_regional():
    # The default substitution must STILL apply when location is unset (None):
    # the backend builds with the us-central1 default and does not raise.
    from obs_captions.config import AppConfig, ProviderConfig
    from obs_captions.stt.registry import create_backend

    cfg = AppConfig(
        engine="google",
        providers={"google": ProviderConfig(mode="speech_v2", project_id=_PROJECT)},
    )
    backend = create_backend(cfg, on_partial=lambda t: None, on_final=lambda t: None)
    assert backend.location == "us-central1"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_natural_stream_end_reopens_and_keeps_feeding_audio():
    # Finding 3: when the server closes the bidi stream on its own (response
    # iterator exhausts) while running, a NEW stream must reopen and later audio
    # must reach it -- not be silently dropped.
    client = FakeSpeechAsyncClient([[CLOSE_STREAM], []])
    backend = _make(client, [], [], restart_interval_s=100.0, reopen_backoff_s=0.01)
    await backend.start_stream()
    try:
        from tests._fake_ws import wait_for

        await wait_for(lambda: len(client.calls) >= 1)
        # First stream ends naturally -> backend must reopen a second stream.
        await wait_for(lambda: len(client.calls) >= 2, timeout=2.0)
        await _drain_call(client, 1)
        # Audio fed AFTER the natural end reaches the reopened stream.
        await backend.feed_audio(b"\x09\x08" * 50)
        await wait_for(lambda: audio_payloads(client.calls[1]) != [], timeout=2.0)
        assert b"".join(audio_payloads(client.calls[1])) == b"\x09\x08" * 50
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_reuse_after_stop_opens_fresh_stream_without_reopen_storm():
    # Bug B: start -> feed -> stop -> start AGAIN on the same backend. The second
    # session must open exactly ONE working stream that receives the new audio. A
    # stale _STOP left on a reused queue makes every new stream terminate at once
    # -> a throttled-but-infinite reopen storm, and the audio never reaches the
    # second session's first stream.
    from tests._fake_ws import wait_for

    client = FakeSpeechAsyncClient()
    backend = _make(client, [], [], restart_interval_s=100.0, reopen_backoff_s=0.01)
    # Session 1: open, deliver audio, stop.
    await backend.start_stream()
    await _drain_call(client, 0)
    await backend.feed_audio(b"\x01\x02" * 10)
    await wait_for(lambda: audio_payloads(client.calls[0]) != [])
    await backend.stop_stream()
    first = len(client.calls)  # streams opened in session 1 (== 1)
    # Session 2: reuse the same backend object.
    await backend.start_stream()
    await _drain_call(client, first)  # the 2nd session's first stream opens
    await backend.feed_audio(b"\x09\x09" * 10)
    # Let any reopen storm manifest (backoff 0.01 -> ~15 reopens in 0.15s if buggy).
    await asyncio.sleep(0.15)
    opened_in_session_2 = len(client.calls) - first
    assert opened_in_session_2 == 1, (
        f"reopen storm: {opened_in_session_2} streams opened in 2nd session"
    )
    # The new audio reaches the 2nd session's first (and only) stream.
    assert b"".join(audio_payloads(client.calls[first])) == b"\x09\x09" * 10
    await backend.stop_stream()


@pytest.mark.asyncio
async def test_no_reopen_after_stop_when_stream_ends_naturally():
    # Lifecycle race: streams keep ending naturally (server closes the bidi
    # stream) and the drive keeps reopening. When stop_stream() arrives while a
    # reopen is pending, NO new stream may open after stop -- the _running-gated
    # drive loop must exit. The reopen count must FREEZE once stopped.
    from tests._fake_ws import wait_for

    client = FakeSpeechAsyncClient(always_close=True)  # every stream ends naturally
    backend = _make(client, [], [], restart_interval_s=100.0, reopen_backoff_s=0.05)
    await backend.start_stream()
    await wait_for(lambda: len(client.calls) >= 2)  # a natural-end reopen happened
    await backend.stop_stream()
    opened = len(client.calls)
    await asyncio.sleep(0.2)  # several backoff windows: a post-stop reopen would show
    assert len(client.calls) == opened  # NO stream opened after stop


@pytest.mark.asyncio
async def test_autobuilt_client_rebuilt_after_stop_not_reused_closed(monkeypatch):
    # Client-lifecycle bug (Codex finding): on the PRODUCTION path the client is
    # auto-built (client=None). stop_stream() closes its transport, so a later
    # start_stream() MUST build a fresh client -- a closed gRPC channel cannot
    # serve a new streaming_recognize (the faithful fake now RAISES after close()).
    # The injected-fake tests never modelled a closed transport, hiding this. This
    # is the client-lifecycle counterpart to the queue/sentinel reuse fix: each
    # start must get a fresh queue AND a rebuilt (owned) client.
    from tests._fake_ws import wait_for

    built: list[FakeSpeechAsyncClient] = []

    def _factory() -> FakeSpeechAsyncClient:
        client = FakeSpeechAsyncClient()
        built.append(client)
        return client

    # client=None -> the backend owns and auto-builds via _build_client, which we
    # stub with a counting factory so we can assert a NEW client per session.
    backend = _make(None, [], [], restart_interval_s=100.0, reopen_backoff_s=0.01)
    monkeypatch.setattr(backend, "_build_client", _factory)

    # Session 1: auto-build client #1, deliver audio, stop (closes its transport).
    await backend.start_stream()
    try:
        await _drain_call(built[0], 0)
        await backend.feed_audio(b"\x01\x02" * 10)
        await wait_for(lambda: audio_payloads(built[0].calls[0]) != [])
    finally:
        await backend.stop_stream()
    assert len(built) == 1
    assert built[0].closed is True  # owned client's transport closed on stop

    # Session 2: the closed client #1 cannot be reused -> a fresh client #2 must be
    # built and must receive the new audio. Buggy code keeps reusing the closed #1
    # (never builds #2), so this wait_for times out -> RED.
    await backend.start_stream()
    try:
        await wait_for(lambda: len(built) == 2)  # a fresh client was built
        await _drain_call(built[1], 0)  # client #2's stream opened + config sent
        await backend.feed_audio(b"\x09\x09" * 10)
        await wait_for(lambda: audio_payloads(built[1].calls[0]) != [])
        assert b"".join(audio_payloads(built[1].calls[0])) == b"\x09\x09" * 10
    finally:
        await backend.stop_stream()


@pytest.mark.asyncio
async def test_immediate_repeated_stream_end_is_throttled_by_backoff():
    # Busy-spin guard: while running, a stream that ends immediately and repeatedly
    # must be throttled by reopen_backoff_s (no 100% CPU spin). The reopen count
    # over a short window is bounded by ~window/backoff, never hundreds.
    client = FakeSpeechAsyncClient(always_close=True)  # every stream ends at once
    backend = _make(client, [], [], restart_interval_s=100.0, reopen_backoff_s=0.05)
    await backend.start_stream()
    await asyncio.sleep(0.3)
    await backend.stop_stream()
    opened = len(client.calls)
    # ~0.3 / 0.05 = 6 reopens; bounded far under a busy-spin (hundreds+).
    assert 2 <= opened <= 15, f"unexpected reopen count: {opened}"
