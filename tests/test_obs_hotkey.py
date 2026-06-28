"""Tests for ObsHotkeyListener and CaptionController — fake injected client, no network."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest

from obs_captions.config import AppConfig, ObsConfig, ObsHotkeyConfig
from obs_captions.obs_hotkey import (
    EVENT_SUBSCRIPTION_INPUTS,
    CaptionController,
    ObsHotkeyListener,
    _build_production_hotkey_client,
)
from obs_captions.pipeline import CaptionState


# ---------------------------------------------------------------------------
# Fake simpleobsws-compatible hotkey client
# ---------------------------------------------------------------------------


@dataclass
class FakeHotkeyResponse:
    _ok: bool
    responseData: dict[str, Any] = field(default_factory=dict)

    def ok(self) -> bool:
        return self._ok


class FakeHotkeyClient:
    """Fake simpleobsws.WebSocketClient with event support."""

    def __init__(
        self,
        *,
        connect_raises: Exception | None = None,
        connect_ok: bool = True,
        call_raises: Exception | None = None,
    ) -> None:
        self.url: str = ""
        self.password: str = ""
        self._connect_raises = connect_raises
        self._connect_ok = connect_ok
        self._call_raises = call_raises
        self.connected = False
        self.identified = False
        self.disconnected = False
        self.calls: list[Any] = []
        self._callbacks: list[tuple[Any, str | None]] = []
        self.register_call_count: int = 0

    async def connect(self) -> bool:
        if self._connect_raises:
            raise self._connect_raises
        self.connected = self._connect_ok
        return self._connect_ok

    async def wait_until_identified(self, timeout: float = 10) -> bool:
        self.identified = True
        return True

    async def call(self, request: Any) -> FakeHotkeyResponse:
        self.calls.append(request)
        if self._call_raises:
            raise self._call_raises
        return FakeHotkeyResponse(_ok=True)

    async def disconnect(self) -> None:
        self.disconnected = True
        self.connected = False

    def register_event_callback(self, callback: Any, event_type: str | None = None) -> None:
        self.register_call_count += 1
        if not asyncio.iscoroutinefunction(callback):
            raise Exception("register_event_callback: callback must be a coroutine function (async def)")
        self._callbacks.append((callback, event_type))

    def deregister_event_callback(self, callback: Any) -> None:
        self._callbacks = [(cb, et) for cb, et in self._callbacks if cb is not callback]

    def fire_event(self, event_type: str, data: Any) -> None:
        """Synchronously invoke matching callbacks (mirrors simpleobsws dispatch)."""
        for cb, et in list(self._callbacks):
            if et is None or et == event_type:
                if asyncio.iscoroutinefunction(cb):
                    asyncio.ensure_future(cb(data))
                else:
                    cb(data)

    @property
    def registered_callback_count(self) -> int:
        return len(self._callbacks)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fast_sleep(_: float) -> None:
    """Yield to the event loop once (no real delay) so other tasks can run."""
    await asyncio.sleep(0)


def _make_config(
    *,
    pause_input: str = "_CaptionPause",
    clear_input: str = "_CaptionClear",
    enabled: bool = True,
) -> AppConfig:
    obs_config = ObsConfig(host="localhost", port=4455, source_name="LiveCaptions")
    hotkey_config = ObsHotkeyConfig(enabled=enabled, pause_input=pause_input, clear_input=clear_input)
    # Use model_copy to inject hotkey into ObsConfig
    obs_with_hotkey = obs_config.model_copy(update={"hotkey": hotkey_config})
    return AppConfig(obs=obs_with_hotkey)


def _make_listener(
    client: FakeHotkeyClient | None = None,
    *,
    pause_input: str = "_P",
    clear_input: str = "_C",
    max_connect_attempts: int = 1,
    ping_interval: float = 0.0,
) -> tuple[ObsHotkeyListener, CaptionController, CaptionState, FakeHotkeyClient]:
    if client is None:
        client = FakeHotkeyClient()
    state = CaptionState()
    controller = CaptionController(state)
    config = _make_config(pause_input=pause_input, clear_input=clear_input)
    listener = ObsHotkeyListener(
        config=config,
        controller=controller,
        client=client,
        sleep_fn=_fast_sleep,
        max_connect_attempts=max_connect_attempts,
        ping_interval=ping_interval,
    )
    return listener, controller, state, client


# ---------------------------------------------------------------------------
# EventSubscription flag
# ---------------------------------------------------------------------------


def test_event_subscription_inputs_flag_equals_8() -> None:
    """The Inputs EventSubscription bitmask must be 1<<3 = 8."""
    assert EVENT_SUBSCRIPTION_INPUTS == (1 << 3)
    assert EVENT_SUBSCRIPTION_INPUTS == 8


# ---------------------------------------------------------------------------
# CaptionController
# ---------------------------------------------------------------------------


def test_caption_controller_initial_state_not_paused() -> None:
    state = CaptionState()
    controller = CaptionController(state)
    assert controller.paused is False


def test_caption_controller_set_paused_true() -> None:
    state = CaptionState()
    controller = CaptionController(state)
    controller.set_paused(True)
    assert controller.paused is True


def test_caption_controller_set_paused_false() -> None:
    state = CaptionState()
    controller = CaptionController(state)
    controller.set_paused(True)
    controller.set_paused(False)
    assert controller.paused is False


def test_caption_controller_clear_calls_state_clear() -> None:
    state = CaptionState()
    from obs_captions.stt import Transcript

    state.on_final(Transcript(text="hello", is_final=True))
    assert state.snapshot().committed == ["hello"]

    controller = CaptionController(state)
    controller.clear()
    assert state.snapshot().committed == []
    assert state.snapshot().partial == ""


# ---------------------------------------------------------------------------
# _on_event: pause sentinel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_input_muted_pauses_controller() -> None:
    """InputMuteStateChanged on pause_input with muted=True → controller.paused=True."""
    listener, controller, _, client = _make_listener(pause_input="_P", clear_input="_C")
    await listener.start()

    client.fire_event("InputMuteStateChanged", {"inputName": "_P", "inputMuted": True})
    await asyncio.sleep(0)  # let the async _on_event coroutine run
    assert controller.paused is True

    await listener.stop()


@pytest.mark.asyncio
async def test_pause_input_unmuted_resumes_controller() -> None:
    """InputMuteStateChanged on pause_input with muted=False → controller.paused=False."""
    listener, controller, _, client = _make_listener(pause_input="_P", clear_input="_C")
    await listener.start()

    controller.set_paused(True)
    client.fire_event("InputMuteStateChanged", {"inputName": "_P", "inputMuted": False})
    await asyncio.sleep(0)  # let the async _on_event coroutine run
    assert controller.paused is False

    await listener.stop()


# ---------------------------------------------------------------------------
# _on_event: clear sentinel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_input_muted_calls_clear_and_resets_sentinel() -> None:
    """Clear sentinel muted → controller.clear() called AND SetInputMute(False) issued."""
    from obs_captions.stt import Transcript

    listener, controller, state, client = _make_listener(pause_input="_P", clear_input="_C")
    await listener.start()

    state.on_final(Transcript(text="existing caption", is_final=True))
    assert state.snapshot().committed == ["existing caption"]

    client.fire_event("InputMuteStateChanged", {"inputName": "_C", "inputMuted": True})

    # Let scheduled _reset_clear_sentinel task run
    for _ in range(4):
        await asyncio.sleep(0)

    # Caption cleared
    assert state.snapshot().committed == []
    assert state.snapshot().partial == ""

    # SetInputMute(False) issued to re-arm sentinel
    mute_calls = [
        c for c in client.calls
        if c.requestType == "SetInputMute"
    ]
    assert len(mute_calls) == 1
    assert mute_calls[0].requestData == {"inputName": "_C", "inputMuted": False}

    await listener.stop()


@pytest.mark.asyncio
async def test_clear_sentinel_rearm_not_skipped_when_subscriber_raises() -> None:
    """try/finally guarantees: even if a CaptionState subscriber raises during
    controller.clear(), SetInputMute(False) is still dispatched to re-arm the sentinel.

    In production simpleobsws catches or isolates callback exceptions; here we
    suppress the propagated RuntimeError from fire_event to mirror that behaviour,
    then verify the finally-scheduled task completes.
    """
    import contextlib
    from obs_captions.stt import Transcript

    listener, controller, state, client = _make_listener(pause_input="_P", clear_input="_C")
    await listener.start()

    state.on_final(Transcript(text="existing caption", is_final=True))

    # Register a subscriber that raises on every state notification.
    def _raising_subscriber(snapshot: Any) -> None:
        raise RuntimeError("subscriber exploded")

    state.subscribe(_raising_subscriber)

    # Fire the clear event — subscriber will raise inside controller.clear().
    # suppress() mirrors simpleobsws not propagating callback exceptions to callers.
    with contextlib.suppress(RuntimeError):
        client.fire_event("InputMuteStateChanged", {"inputName": "_C", "inputMuted": True})

    # Drain the event loop so the finally-scheduled _reset_clear_sentinel task runs.
    for _ in range(4):
        await asyncio.sleep(0)

    # SetInputMute(False) must still have been dispatched despite the subscriber exception.
    mute_calls = [c for c in client.calls if c.requestType == "SetInputMute"]
    assert len(mute_calls) == 1, (
        "SetInputMute(False) must be issued even when a subscriber raises during clear()"
    )
    assert mute_calls[0].requestData == {"inputName": "_C", "inputMuted": False}

    await listener.stop()


@pytest.mark.asyncio
async def test_clear_input_unmuted_does_nothing() -> None:
    """Clear sentinel unmuted (muted=False) → no clear, no reset request."""
    from obs_captions.stt import Transcript

    listener, controller, state, client = _make_listener(pause_input="_P", clear_input="_C")
    await listener.start()

    state.on_final(Transcript(text="keep me", is_final=True))
    client.fire_event("InputMuteStateChanged", {"inputName": "_C", "inputMuted": False})

    for _ in range(4):
        await asyncio.sleep(0)

    # Caption unchanged, no SetInputMute called
    assert state.snapshot().committed == ["keep me"]
    mute_calls = [c for c in client.calls if c.requestType == "SetInputMute"]
    assert mute_calls == []

    await listener.stop()


@pytest.mark.asyncio
async def test_unrelated_input_name_ignored() -> None:
    """Events for inputs other than pause/clear sentinels are silently ignored."""
    listener, controller, _, client = _make_listener(pause_input="_P", clear_input="_C")
    await listener.start()

    controller.set_paused(False)
    client.fire_event("InputMuteStateChanged", {"inputName": "Microphone", "inputMuted": True})

    assert controller.paused is False

    await listener.stop()


@pytest.mark.asyncio
async def test_bad_event_data_attribute_error_is_swallowed() -> None:
    """Events with unexpected data shape don't crash the listener."""
    listener, _, _, client = _make_listener()
    await listener.start()

    # Fire with a non-dict object — subscript access raises TypeError, caught by guard.
    client.fire_event("InputMuteStateChanged", object())  # not subscriptable → TypeError

    # No exception should propagate
    await listener.stop()


# ---------------------------------------------------------------------------
# _reset_clear_sentinel edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_clear_sentinel_noop_when_stopped() -> None:
    """_reset_clear_sentinel is a no-op when listener is stopped."""
    listener, _, _, client = _make_listener()
    await listener.start()
    listener._stopped = True

    await listener._reset_clear_sentinel()

    mute_calls = [c for c in client.calls if c.requestType == "SetInputMute"]
    assert mute_calls == []

    # Restore for clean stop
    listener._stopped = False
    await listener.stop()


@pytest.mark.asyncio
async def test_reset_clear_sentinel_noop_when_client_none() -> None:
    """_reset_clear_sentinel is a no-op when _client is None."""
    listener, _, _, _ = _make_listener()
    await listener.start()
    listener._client = None

    await listener._reset_clear_sentinel()  # must not raise

    await listener.stop()


@pytest.mark.asyncio
async def test_reset_clear_sentinel_logs_on_exception(caplog: pytest.LogCaptureFixture) -> None:
    """If the SetInputMute call raises, the exception is caught and logged."""

    class RaisingClient(FakeHotkeyClient):
        async def call(self, request: Any) -> FakeHotkeyResponse:
            self.calls.append(request)
            if request.requestType == "SetInputMute":
                raise OSError("call failed")
            return FakeHotkeyResponse(_ok=True)

    listener, _, _, _ = _make_listener(client=RaisingClient())
    await listener.start()

    with caplog.at_level(logging.WARNING):
        await listener._reset_clear_sentinel()

    assert any("reset clear sentinel failed" in r.getMessage() for r in caplog.records)

    await listener.stop()


@pytest.mark.asyncio
async def test_reset_clear_sentinel_logs_on_not_ok_response(caplog: pytest.LogCaptureFixture) -> None:
    """If SetInputMute returns a not-ok response, the warning at line 329 is logged.

    This exercises the branch: ``if not resp.ok(): logger.warning(...)``
    which is reached when OBS returns an error code for the re-arm call
    (e.g. the sentinel source was renamed or removed in OBS).
    """

    class NotOkMuteClient(FakeHotkeyClient):
        async def call(self, request: Any) -> FakeHotkeyResponse:
            self.calls.append(request)
            if request.requestType == "SetInputMute":
                return FakeHotkeyResponse(_ok=False)
            return FakeHotkeyResponse(_ok=True)

    listener, _, _, _ = _make_listener(client=NotOkMuteClient())
    await listener.start()

    with caplog.at_level(logging.WARNING):
        await listener._reset_clear_sentinel()

    assert any("SetInputMute reset returned not-ok" in r.getMessage() for r in caplog.records)

    await listener.stop()


@pytest.mark.asyncio
async def test_stop_after_clear_event_completes_rearm() -> None:
    """stop() drains _reset_task so the clear sentinel is re-armed even in a teardown race.

    Scenario: a clear event fires (scheduling _reset_clear_sentinel via create_task)
    and stop() is called immediately before the task runs. The re-arm SetInputMute(False)
    must still be issued because stop() awaits the in-flight task before marking _stopped.
    """
    from obs_captions.stt import Transcript

    listener, _, state, client = _make_listener(pause_input="_P", clear_input="_C")
    await listener.start()

    state.on_final(Transcript(text="caption", is_final=True))

    # Fire the clear event — this schedules _reset_clear_sentinel but does NOT await it.
    client.fire_event("InputMuteStateChanged", {"inputName": "_C", "inputMuted": True})

    # Yield once so the _on_event coroutine runs and creates the task, but the
    # task itself has not yet had a chance to execute its body.
    await asyncio.sleep(0)

    # stop() must drain the reset task before setting _stopped=True.
    await listener.stop()

    # The re-arm SetInputMute(False) must have been issued despite the immediate stop.
    mute_calls = [c for c in client.calls if c.requestType == "SetInputMute"]
    assert len(mute_calls) == 1, (
        "stop() must await _reset_task so the clear sentinel is re-armed on teardown"
    )
    assert mute_calls[0].requestData == {"inputName": "_C", "inputMuted": False}


# ---------------------------------------------------------------------------
# _connect_with_retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_with_retry_success_first_attempt() -> None:
    listener, _, _, client = _make_listener(max_connect_attempts=3)
    exc = await listener._connect_with_retry()
    assert exc is None
    assert client.connected
    await listener.stop()


@pytest.mark.asyncio
async def test_connect_with_retry_returns_exception_after_all_fail() -> None:
    client = FakeHotkeyClient(connect_raises=OSError("refused"))
    listener, _, _, _ = _make_listener(client=client, max_connect_attempts=2)
    listener._client = client  # inject manually since start() not called

    exc = await listener._connect_with_retry()
    assert exc is not None
    assert isinstance(exc, OSError)


@pytest.mark.asyncio
async def test_connect_with_retry_backoff_delays_double() -> None:
    """Exponential backoff: each delay is >= the previous."""
    client = FakeHotkeyClient(connect_raises=OSError("down"))
    delays: list[float] = []

    async def recording_sleep(s: float) -> None:
        delays.append(s)

    listener, _, _, _ = _make_listener(client=client, max_connect_attempts=4)
    listener._sleep_fn = recording_sleep
    listener._client = client

    await listener._connect_with_retry()

    assert len(delays) == 3
    for i in range(1, len(delays)):
        assert delays[i] >= delays[i - 1]


@pytest.mark.asyncio
async def test_connect_with_retry_returns_exception_when_connect_returns_false() -> None:
    """connect() returning False triggers ConnectionError (line 230 coverage)."""
    client = FakeHotkeyClient(connect_ok=False)
    listener, _, _, _ = _make_listener(client=client, max_connect_attempts=1)
    listener._client = client

    exc = await listener._connect_with_retry()
    assert isinstance(exc, ConnectionError)


# ---------------------------------------------------------------------------
# start() / stop() lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_connects_and_registers_callback() -> None:
    """start() must connect and register the InputMuteStateChanged callback."""
    listener, _, _, client = _make_listener()
    await listener.start()

    assert client.connected
    assert client.identified
    assert client.registered_callback_count == 1

    await listener.stop()


@pytest.mark.asyncio
async def test_start_soft_failure_when_initial_connect_fails() -> None:
    """start() returns without raising when initial connect fails (soft degradation)."""
    client = FakeHotkeyClient(connect_raises=OSError("refused"))
    listener, _, _, _ = _make_listener(client=client, max_connect_attempts=1)

    await listener.start()  # must not raise

    # No callback registered since connection failed
    assert client.registered_callback_count == 0


@pytest.mark.asyncio
async def test_stop_cancels_task_and_disconnects() -> None:
    """stop() cancels the keepalive task and disconnects the client."""
    listener, _, _, client = _make_listener()
    await listener.start()

    assert listener._listener_task is not None

    await listener.stop()

    assert listener._stopped is True
    assert client.disconnected


@pytest.mark.asyncio
async def test_stop_swallows_disconnect_exception() -> None:
    """stop() must not raise even if disconnect() throws."""

    class DisconnectRaisesClient(FakeHotkeyClient):
        async def disconnect(self) -> None:
            raise RuntimeError("disconnect blew up")

    listener, _, _, _ = _make_listener(client=DisconnectRaisesClient())
    await listener.start()
    await listener.stop()  # must not raise


# ---------------------------------------------------------------------------
# _ping_until_disconnect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_until_disconnect_returns_false_when_stopped() -> None:
    """When _stopped=True, ping loop exits cleanly (returns False = not disconnected)."""
    listener, _, _, client = _make_listener()
    await listener.start()
    listener._stopped = True

    result = await listener._ping_until_disconnect()
    assert result is False

    listener._stopped = False
    await listener.stop()


@pytest.mark.asyncio
async def test_ping_until_disconnect_returns_true_on_exception() -> None:
    """A call() exception signals disconnect → returns True."""

    class OneFailClient(FakeHotkeyClient):
        async def call(self, request: Any) -> FakeHotkeyResponse:
            self.calls.append(request)
            if request.requestType == "GetVersion":
                raise OSError("connection lost")
            return FakeHotkeyResponse(_ok=True)

    listener, _, _, client = _make_listener(client=OneFailClient(), ping_interval=0.0)
    await listener.start()

    result = await listener._ping_until_disconnect()
    assert result is True

    await listener.stop()


@pytest.mark.asyncio
async def test_ping_until_disconnect_returns_true_on_not_ok_response() -> None:
    """A not-ok GetVersion response signals disconnect → returns True."""

    class NotOkClient(FakeHotkeyClient):
        async def call(self, request: Any) -> FakeHotkeyResponse:
            self.calls.append(request)
            if request.requestType == "GetVersion":
                return FakeHotkeyResponse(_ok=False)
            return FakeHotkeyResponse(_ok=True)

    listener, _, _, client = _make_listener(client=NotOkClient(), ping_interval=0.0)
    await listener.start()

    result = await listener._ping_until_disconnect()
    assert result is True

    await listener.stop()


# ---------------------------------------------------------------------------
# _run_keepalive: reconnect paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_keepalive_reconnects_and_resubscribes(caplog: pytest.LogCaptureFixture) -> None:
    """After a disconnect, keepalive reconnects and re-registers the callback."""
    ping_call_count = [0]

    async def alternating_ping() -> bool:
        ping_call_count[0] += 1
        if ping_call_count[0] == 1:
            return True  # first call: simulate disconnect
        return False  # second call: clean exit (not disconnected)

    listener, _, _, client = _make_listener(max_connect_attempts=2, ping_interval=0.0)
    await listener.start()

    # Inject mocks
    listener._ping_until_disconnect = alternating_ping  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING):
        await listener._run_keepalive()

    # Client connected twice: once via start(), once via reconnect
    assert client.connected is True
    # register_event_callback must have been called twice: once in start() and once
    # after reconnect in _run_keepalive(). A count of >= 1 would pass even without
    # re-registration; asserting exactly 2 isolates the reconnect re-subscribe path.
    assert client.register_call_count == 2

    await listener.stop()


@pytest.mark.asyncio
async def test_run_keepalive_exits_if_reconnect_fails(caplog: pytest.LogCaptureFixture) -> None:
    """If reconnect fails after disconnect, keepalive logs and exits."""

    async def instant_disconnect() -> bool:
        return True  # always report disconnect

    async def always_fail() -> Exception:
        return OSError("reconnect permanently failed")

    listener, _, _, client = _make_listener()
    await listener.start()

    listener._ping_until_disconnect = instant_disconnect  # type: ignore[method-assign]
    listener._connect_with_retry = always_fail  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR):
        await listener._run_keepalive()  # should exit after reconnect fails

    assert any("reconnect failed" in r.getMessage() for r in caplog.records)

    await listener.stop()


# ---------------------------------------------------------------------------
# Reconnect integration: callback persists after reconnect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnect_callback_fires_after_reconnect() -> None:
    """After a reconnect cycle, events still reach the controller."""

    class ReconnectClient(FakeHotkeyClient):
        def __init__(self) -> None:
            super().__init__()
            self._connect_count = 0
            self._heartbeat_count = 0

        async def connect(self) -> bool:
            self._connect_count += 1
            self.connected = True
            return True

        async def call(self, request: Any) -> FakeHotkeyResponse:
            self.calls.append(request)
            if request.requestType == "GetVersion":
                self._heartbeat_count += 1
                if self._heartbeat_count == 1:
                    raise OSError("connection lost")
            return FakeHotkeyResponse(_ok=True)

    client = ReconnectClient()
    listener, controller, _, _ = _make_listener(
        client=client,
        max_connect_attempts=2,
        ping_interval=0.0,
    )
    listener._client = client  # ensure client is set for re-use
    await listener.start()

    # Wait for reconnect to complete (connect_count reaches 2)
    async def wait_for_reconnect() -> None:
        while client._connect_count < 2:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_reconnect(), timeout=1.0)

    # Fire event; callback should be registered again
    client.fire_event("InputMuteStateChanged", {"inputName": "_P", "inputMuted": True})
    await asyncio.sleep(0)  # let the async _on_event coroutine run
    assert controller.paused is True

    await listener.stop()


# ---------------------------------------------------------------------------
# Default-off: hotkey.enabled=False
# ---------------------------------------------------------------------------


def test_hotkey_config_defaults_to_disabled() -> None:
    """ObsHotkeyConfig.enabled defaults to False (feature is opt-in)."""
    cfg = ObsHotkeyConfig()
    assert cfg.enabled is False


def test_hotkey_config_default_pause_input() -> None:
    assert ObsHotkeyConfig().pause_input == "_CaptionPause"


def test_hotkey_config_default_clear_input() -> None:
    assert ObsHotkeyConfig().clear_input == "_CaptionClear"


def test_obs_config_has_hotkey_field() -> None:
    """ObsConfig must expose a .hotkey field of type ObsHotkeyConfig."""
    cfg = ObsConfig()
    assert isinstance(cfg.hotkey, ObsHotkeyConfig)
    assert cfg.hotkey.enabled is False


def test_app_config_obs_hotkey_round_trips() -> None:
    """AppConfig fully validates with obs.hotkey section."""
    cfg = AppConfig.model_validate(
        {"obs": {"hotkey": {"enabled": True, "pause_input": "_P", "clear_input": "_C"}}}
    )
    assert cfg.obs.hotkey.enabled is True
    assert cfg.obs.hotkey.pause_input == "_P"


def test_hotkey_config_extra_fields_forbidden() -> None:
    """ObsHotkeyConfig must reject unknown fields (extra='forbid')."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        ObsHotkeyConfig(unknown_field=True)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# _build_production_hotkey_client
# ---------------------------------------------------------------------------


def test_build_production_hotkey_client_calls_simpleobsws() -> None:
    """_build_production_hotkey_client delegates to simpleobsws.WebSocketClient."""
    from unittest.mock import MagicMock

    fake_params_cls = MagicMock()
    fake_ws_cls = MagicMock()
    fake_module = MagicMock()
    fake_module.IdentificationParameters = fake_params_cls
    fake_module.WebSocketClient = fake_ws_cls

    with patch.dict("sys.modules", {"simpleobsws": fake_module}):
        result = _build_production_hotkey_client("ws://localhost:4455", "s3cr3t")

    fake_params_cls.assert_called_once_with(eventSubscriptions=EVENT_SUBSCRIPTION_INPUTS)
    fake_ws_cls.assert_called_once()
    assert result is fake_ws_cls.return_value


def test_build_production_hotkey_client_raises_without_simpleobsws() -> None:
    """_build_production_hotkey_client raises RuntimeError when simpleobsws is missing."""
    with patch.dict("sys.modules", {"simpleobsws": None}):
        with pytest.raises(RuntimeError, match="simpleobsws not installed"):
            _build_production_hotkey_client("ws://localhost:4455", "")


def test_on_event_is_coroutine_function() -> None:
    """_on_event must be async so simpleobsws register_event_callback accepts it.

    simpleobsws enforces iscoroutinefunction(callback) and raises
    EventRegistrationError for sync callbacks. This test catches regressions.
    """
    listener, _, _, _ = _make_listener()
    assert asyncio.iscoroutinefunction(listener._on_event), (
        "_on_event must be 'async def' — simpleobsws rejects synchronous callbacks"
    )
