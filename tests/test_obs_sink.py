"""Tests for ObsTextSink — all mock-based, no real OBS required."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable
from unittest.mock import MagicMock, patch

import pytest

from obs_captions.config import AppConfig, ObsConfig
from obs_captions.obs_sink import ObsTextSink, _make_production_request
from obs_captions.pipeline import CaptionSnapshot, CaptionState


# ---------------------------------------------------------------------------
# Fake simpleobsws-compatible client
# ---------------------------------------------------------------------------


@dataclass
class FakeStatus:
    result: bool
    code: int = 100
    comment: str | None = None


@dataclass
class FakeResponse:
    _ok: bool
    responseData: dict[str, Any] = field(default_factory=dict)
    requestStatus: FakeStatus = field(default_factory=lambda: FakeStatus(result=True))

    def ok(self) -> bool:
        return self._ok

    def has_data(self) -> bool:
        return bool(self.responseData)


class FakeRequest:
    """Mirrors simpleobsws.Request — records (requestType, requestData)."""

    def __init__(self, requestType: str, requestData: dict[str, Any] | None = None) -> None:
        self.requestType = requestType
        self.requestData = requestData or {}


class FakeWsClient:
    """Fake simpleobsws.WebSocketClient."""

    def __init__(
        self,
        *,
        url: str = "",
        password: str = "",
        inputs: list[str] | None = None,
        connect_raises: Exception | None = None,
    ) -> None:
        self.url = url
        self.password = password
        self._inputs: list[str] = inputs if inputs is not None else []
        self._connect_raises = connect_raises
        self.connected = False
        self.identified = False
        self.calls: list[FakeRequest] = []
        self.disconnected = False

    async def connect(self) -> bool:
        if self._connect_raises:
            raise self._connect_raises
        self.connected = True
        return True

    async def wait_until_identified(self, timeout: float = 10) -> bool:
        self.identified = True
        return True

    async def call(self, request: FakeRequest) -> FakeResponse:
        self.calls.append(request)
        if request.requestType == "GetInputList":
            return FakeResponse(
                _ok=True,
                responseData={
                    "inputs": [
                        {"inputName": name, "inputKind": "text_ft2_source_v2"}
                        for name in self._inputs
                    ]
                },
            )
        return FakeResponse(_ok=True, responseData={})

    async def disconnect(self) -> None:
        self.disconnected = True
        self.connected = False


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _partial(text: str):
    from obs_captions.stt import Transcript

    return Transcript(text=text, is_final=False)


async def _noop_sleep(_seconds: float) -> None:
    return None


def _make_sink(
    client: FakeWsClient,
    *,
    source_name: str = "LiveCaptions",
    debounce_ms: int = 0,
    max_connect_attempts: int = 4,
    initial_delay: float = 0.5,
    max_delay: float = 30.0,
    backoff_multiplier: float = 2.0,
    jitter: float = 0.0,
    jitter_fn: Callable[[], float] | None = None,
) -> ObsTextSink:
    obs_config = ObsConfig(
        host="localhost",
        port=4455,
        source_name=source_name,
        reconnect_max_attempts=max_connect_attempts,
        reconnect_initial_delay=initial_delay,
        reconnect_max_delay=max_delay,
        reconnect_backoff_multiplier=backoff_multiplier,
        reconnect_jitter=jitter,
    )
    app_config = AppConfig(obs=obs_config)
    state = CaptionState(max_lines=3)
    return ObsTextSink(
        state=state,
        config=app_config,
        client=client,
        debounce_ms=debounce_ms,
        max_connect_attempts=max_connect_attempts,
        initial_delay=initial_delay,
        max_delay=max_delay,
        backoff_multiplier=backoff_multiplier,
        jitter=jitter,
        jitter_fn=jitter_fn,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_uses_host_port_password():
    """start() should build url from host/port and pass password."""
    client = FakeWsClient()
    sink = _make_sink(client)
    # Patch _obs_ws_password so we don't need env
    sink._password = "s3cr3t"

    await sink.start()
    await sink.stop()

    assert "localhost" in client.url
    assert "4455" in client.url
    assert client.password == "s3cr3t"


@pytest.mark.asyncio
async def test_create_input_called_when_source_missing():
    """If source not in GetInputList, CreateInput must be called."""
    client = FakeWsClient(inputs=[])  # source absent
    sink = _make_sink(client, source_name="LiveCaptions")
    sink._password = ""

    await sink.start()
    await sink.stop()

    types = [c.requestType for c in client.calls]
    assert "GetInputList" in types
    assert "CreateInput" in types
    create = next(c for c in client.calls if c.requestType == "CreateInput")
    assert create.requestData["inputName"] == "LiveCaptions"
    assert create.requestData["inputKind"] == "text_ft2_source_v2"


@pytest.mark.asyncio
async def test_create_input_not_called_when_source_present():
    """If source already exists, CreateInput must NOT be called."""
    client = FakeWsClient(inputs=["LiveCaptions"])
    sink = _make_sink(client, source_name="LiveCaptions")
    sink._password = ""

    await sink.start()
    await sink.stop()

    types = [c.requestType for c in client.calls]
    assert "GetInputList" in types
    assert "CreateInput" not in types


@pytest.mark.asyncio
async def test_caption_change_triggers_set_input_settings():
    """on_change → SetInputSettings called with expected text."""
    client = FakeWsClient(inputs=["LiveCaptions"])
    sink = _make_sink(client, source_name="LiveCaptions", debounce_ms=0)
    sink._password = ""

    await sink.start()

    # Push a snapshot manually via the registered on_change
    snapshot = CaptionSnapshot(committed=["안녕하세요"], partial="여러분")
    await sink._send_snapshot(snapshot)

    await sink.stop()

    set_calls = [c for c in client.calls if c.requestType == "SetInputSettings"]
    assert len(set_calls) >= 1
    last = set_calls[-1]
    assert last.requestData["inputName"] == "LiveCaptions"
    text = last.requestData["inputSettings"]["text"]
    assert "안녕하세요" in text
    assert "여러분" in text


@pytest.mark.asyncio
async def test_debounce_coalesces_rapid_partials():
    """Multiple rapid changes with debounce_ms > 0 → only latest pushed."""
    client = FakeWsClient(inputs=["LiveCaptions"])
    sink = _make_sink(client, source_name="LiveCaptions", debounce_ms=50)
    sink._password = ""

    await sink.start()

    # Schedule 5 rapid snapshots; only the last should be sent
    for i in range(5):
        sink._schedule_update(CaptionSnapshot(committed=[], partial=f"partial-{i}"))

    # Wait longer than debounce
    await asyncio.sleep(0.15)
    await sink.stop()

    set_calls = [c for c in client.calls if c.requestType == "SetInputSettings"]
    # Should have sent at most 1 (latest), definitely not 5 separate calls
    assert len(set_calls) <= 2  # allow one intermediate at most
    if set_calls:
        last_text = set_calls[-1].requestData["inputSettings"]["text"]
        assert "partial-4" in last_text


@pytest.mark.asyncio
async def test_connect_failure_raises_after_all_retries():
    """If all connect attempts fail, start() raises ConnectionError with a clear message."""
    client = FakeWsClient(connect_raises=OSError("refused"))
    # Use a no-op sleep so the test is fast; only 1 attempt so no delay needed
    sink = _make_sink(client, max_connect_attempts=1)
    sink._sleep_fn = asyncio.sleep  # will be bypassed since 1 attempt
    sink._password = ""

    with pytest.raises(ConnectionError, match="obs-websocket unreachable"):
        await sink.start()

    assert not client.connected


@pytest.mark.asyncio
async def test_connect_succeeds_after_retries():
    """connect fails N-1 times then succeeds — sink is connected."""
    call_count = 0
    fail_until = 2

    class RetryClient(FakeWsClient):
        async def connect(self) -> bool:
            nonlocal call_count
            call_count += 1
            if call_count < fail_until:
                raise OSError("transient")
            self.connected = True
            return True

    client = RetryClient(inputs=[])
    delays: list[float] = []

    async def fast_sleep(s: float) -> None:
        delays.append(s)

    obs_config = ObsConfig(host="localhost", port=4455, source_name="LiveCaptions")
    app_config = AppConfig(obs=obs_config)
    state = CaptionState(max_lines=3)
    sink = ObsTextSink(
        state=state,
        config=app_config,
        client=client,
        debounce_ms=0,
        max_connect_attempts=4,
        sleep_fn=fast_sleep,
    )
    sink._password = ""

    await sink.start()
    await sink.stop()

    assert client.connected or client.disconnected  # connected at some point
    assert call_count == fail_until  # failed once, succeeded on second
    assert len(delays) == fail_until - 1  # one sleep between attempts


@pytest.mark.asyncio
async def test_connect_backoff_delays_increase():
    """Backoff delays double between retries (exponential)."""
    attempts = 0
    max_attempts = 4

    class AlwaysFailClient(FakeWsClient):
        async def connect(self) -> bool:
            nonlocal attempts
            attempts += 1
            raise OSError("refused")

    client = AlwaysFailClient()
    delays: list[float] = []

    async def record_sleep(s: float) -> None:
        delays.append(s)

    obs_config = ObsConfig(host="localhost", port=4455, source_name="LiveCaptions")
    app_config = AppConfig(obs=obs_config)
    state = CaptionState(max_lines=3)
    sink = ObsTextSink(
        state=state,
        config=app_config,
        client=client,
        debounce_ms=0,
        max_connect_attempts=max_attempts,
        sleep_fn=record_sleep,
    )
    sink._password = ""

    with pytest.raises(ConnectionError):
        await sink.start()

    assert attempts == max_attempts
    # Each delay should be >= the previous (exponential growth)
    assert len(delays) == max_attempts - 1
    for i in range(1, len(delays)):
        assert delays[i] >= delays[i - 1]


@pytest.mark.asyncio
async def test_connect_backoff_delays_exact_without_jitter():
    """With jitter off, delay sequence is exactly exponential with the defaults."""
    attempts = 0
    max_attempts = 5

    class AlwaysFailClient(FakeWsClient):
        async def connect(self) -> bool:
            nonlocal attempts
            attempts += 1
            raise OSError("refused")

    client = AlwaysFailClient()
    delays: list[float] = []

    async def record_sleep(s: float) -> None:
        delays.append(s)

    obs_config = ObsConfig(host="localhost", port=4455, source_name="LiveCaptions")
    app_config = AppConfig(obs=obs_config)
    state = CaptionState(max_lines=3)
    sink = ObsTextSink(
        state=state,
        config=app_config,
        client=client,
        debounce_ms=0,
        max_connect_attempts=max_attempts,
        sleep_fn=record_sleep,
    )
    sink._password = ""

    with pytest.raises(ConnectionError):
        await sink.start()

    assert attempts == max_attempts
    assert delays == [0.5, 1.0, 2.0, 4.0]


@pytest.mark.asyncio
async def test_connect_backoff_delays_apply_jitter_within_expected_range():
    """Jitter adds a bounded extra sleep without changing exponential base growth."""
    attempts = 0
    max_attempts = 4
    max_delay = 30.0

    class AlwaysFailClient(FakeWsClient):
        async def connect(self) -> bool:
            nonlocal attempts
            attempts += 1
            raise OSError("refused")

    client = AlwaysFailClient()
    delays: list[float] = []

    async def record_sleep(s: float) -> None:
        delays.append(s)

    obs_config = ObsConfig(host="localhost", port=4455, source_name="LiveCaptions")
    app_config = AppConfig(obs=obs_config)
    state = CaptionState(max_lines=3)
    sink = ObsTextSink(
        state=state,
        config=app_config,
        client=client,
        debounce_ms=0,
        max_connect_attempts=max_attempts,
        initial_delay=0.5,
        max_delay=max_delay,
        backoff_multiplier=2.0,
        jitter=0.5,
        jitter_fn=lambda: 1.0,
        sleep_fn=record_sleep,
    )
    sink._password = ""

    with pytest.raises(ConnectionError):
        await sink.start()

    assert attempts == max_attempts
    assert len(delays) == max_attempts - 1

    base_delay = 0.5
    expected_bases: list[float] = []
    for _ in range(max_attempts - 1):
        expected_bases.append(base_delay)
        base_delay = min(base_delay * 2.0, max_delay)

    for slept, base in zip(delays, expected_bases, strict=True):
        assert base <= slept <= base + base * 0.5
        assert slept == pytest.approx(base + base * 0.5)


def test_make_sink_wire_reconnect_config_to_constructor_params():
    """Sink fields match reconnection config values used by caller construction."""
    client = FakeWsClient()
    jitter_fn_called: dict[str, int] = {"count": 0}

    def fixed_jitter() -> float:
        jitter_fn_called["count"] += 1
        return 0.7

    sink = _make_sink(
        client,
        max_connect_attempts=7,
        initial_delay=0.75,
        max_delay=12.5,
        backoff_multiplier=1.5,
        jitter=0.25,
        jitter_fn=fixed_jitter,
    )

    assert sink._max_connect_attempts == 7
    assert sink._initial_delay == 0.75
    assert sink._max_delay == 12.5
    assert sink._backoff_multiplier == 1.5
    assert sink._jitter == 0.25
    assert jitter_fn_called["count"] == 0
    assert sink._jitter_fn() == pytest.approx(0.7)
    assert jitter_fn_called["count"] == 1


@pytest.mark.asyncio
async def test_stop_disconnects_client():
    client = FakeWsClient(inputs=[])
    sink = _make_sink(client)
    sink._password = ""

    await sink.start()
    await sink.stop()

    assert client.disconnected


@pytest.mark.asyncio
async def test_start_subscribes_and_stop_unsubscribes_without_clobbering():
    """start() must subscribe (not clobber on_change); stop() unsubscribes only itself."""
    client = FakeWsClient(inputs=["LiveCaptions"])
    obs_config = ObsConfig(host="localhost", port=4455, source_name="LiveCaptions")
    app_config = AppConfig(obs=obs_config)
    state = CaptionState(max_lines=3)

    other_hits: list[Any] = []
    state.subscribe(other_hits.append)

    sink = ObsTextSink(state=state, config=app_config, client=client, debounce_ms=0)
    sink._password = ""

    await sink.start()
    # Both the pre-existing subscriber and the sink are notified.
    state.on_partial(_partial("안녕"))
    for _ in range(4):
        await asyncio.sleep(0)

    assert other_hits[-1] == CaptionSnapshot(committed=[], partial="안녕")
    set_calls = [c for c in client.calls if c.requestType == "SetInputSettings"]
    assert len(set_calls) >= 1

    await sink.stop()
    # After stop, the OTHER subscriber still fires (sink only removed itself).
    state.on_partial(_partial("다음"))
    assert other_hits[-1] == CaptionSnapshot(committed=[], partial="다음")


@pytest.mark.asyncio
async def test_schedule_update_task_exception_surfaced_to_done_callback(caplog):
    """Fire-and-forget _send_snapshot exceptions must be logged, not swallowed."""
    import logging

    client = FakeWsClient(inputs=["LiveCaptions"])
    sink = _make_sink(client, source_name="LiveCaptions", debounce_ms=0)
    sink._password = ""
    await sink.start()

    send_started = asyncio.Event()
    release = asyncio.Event()

    async def boom(_snapshot: Any) -> None:
        send_started.set()
        await release.wait()
        raise RuntimeError("send failed")

    sink._send_snapshot = boom  # type: ignore[assignment]

    with caplog.at_level(logging.ERROR):
        sink._schedule_update(CaptionSnapshot(committed=[], partial="x"))
        # While the send is in flight (before it completes) the task ref must be
        # retained — otherwise asyncio could GC it and lose the exception.
        await asyncio.wait_for(send_started.wait(), timeout=1.0)
        assert len(sink._pending_tasks) == 1  # NON-EMPTY while pending
        release.set()
        for _ in range(4):
            await asyncio.sleep(0)

    await sink.stop()
    # The sink's OWN done-callback must surface it (not asyncio's GC-time default
    # "Task exception was never retrieved"), so the task ref must be retained.
    assert sink._pending_tasks == set()  # task ref discarded on done
    assert any(
        r.name == "obs_captions.obs_sink" and "obs sink background task failed" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_on_task_done_logs_before_discarding_from_pending_tasks(caplog):
    """GAP-A: _on_task_done must log the exception BEFORE discarding the task from
    _pending_tasks. FAILS if discard happens before logger.error is called."""
    import logging

    client = FakeWsClient(inputs=["LiveCaptions"])
    sink = _make_sink(client, source_name="LiveCaptions", debounce_ms=0)
    sink._password = ""
    await sink.start()

    release = asyncio.Event()
    started = asyncio.Event()

    async def boom(_snapshot: Any) -> None:
        started.set()
        await release.wait()
        raise RuntimeError("ordering probe")

    sink._send_snapshot = boom  # type: ignore[assignment]

    tasks_size_at_log_time: list[int] = []

    real_logger = __import__("logging").getLogger("obs_captions.obs_sink")

    original_error = real_logger.error

    def spy_error(msg, *args, **kwargs):
        # Capture the size of _pending_tasks at the exact moment logger.error fires.
        tasks_size_at_log_time.append(len(sink._pending_tasks))
        original_error(msg, *args, **kwargs)

    with caplog.at_level(logging.ERROR):
        sink._schedule_update(CaptionSnapshot(committed=[], partial="x"))
        await asyncio.wait_for(started.wait(), timeout=1.0)

        # Task is in _pending_tasks while in-flight — patch now so we capture state
        # at the moment logger.error is called inside _on_task_done.
        with patch.object(real_logger, "error", spy_error):
            release.set()
            for _ in range(8):
                await asyncio.sleep(0)

    await sink.stop()

    # The spy must have fired (exception was logged).
    assert tasks_size_at_log_time, "logger.error was never called — fix not in place"
    # At log time the task must still be present (log-before-discard ordering).
    assert tasks_size_at_log_time[0] >= 1, (
        "task was already discarded before logger.error fired — ordering is wrong"
    )
    # After the done-callback completes the set must be empty.
    assert sink._pending_tasks == set()


@pytest.mark.asyncio
async def test_debounce_send_exception_is_logged(caplog):
    """debounce_ms > 0: a debounce send whose _send_snapshot raises must be LOGGED.

    Defect 1: the debounce path (production default) previously created the task
    via asyncio.ensure_future with no exception-logging done-callback, so a raise
    from _send_snapshot was silently swallowed until stop(). This test FAILS if
    the Defect-1 fix is reverted.
    """
    import logging

    client = FakeWsClient(inputs=["LiveCaptions"])
    sink = _make_sink(client, source_name="LiveCaptions", debounce_ms=10)
    sink._password = ""
    await sink.start()

    async def boom(_snapshot: Any) -> None:
        raise RuntimeError("debounce send failed")

    sink._send_snapshot = boom  # type: ignore[assignment]

    with caplog.at_level(logging.ERROR):
        sink._schedule_update(CaptionSnapshot(committed=[], partial="x"))
        # Wait past the debounce window so _debounce_send invokes _send_snapshot.
        await asyncio.sleep(0.05)
        for _ in range(4):
            await asyncio.sleep(0)

    await sink.stop()
    assert any(
        r.name == "obs_captions.obs_sink" and "obs sink background task failed" in r.getMessage()
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Mid-session reconnect tests (Fix B)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_failure_marks_disconnected_and_reconnects():
    """A SetInputSettings failure marks the sink disconnected, reconnects, and the
    next send works again."""
    fail = {"on": True}

    class FlakyClient(FakeWsClient):
        async def call(self, request: FakeRequest) -> FakeResponse:
            if request.requestType == "SetInputSettings" and fail["on"]:
                self.calls.append(request)
                raise OSError("connection lost")
            return await super().call(request)

    client = FlakyClient(inputs=["LiveCaptions"])
    sink = _make_sink(client, source_name="LiveCaptions", debounce_ms=0)
    sink._sleep_fn = _noop_sleep
    sink._password = ""
    await sink.start()

    # First send fails -> triggers reconnect.
    await sink._send_snapshot(CaptionSnapshot(committed=["안녕하세요"], partial="여러분"))
    # Reconnect re-ran connect; sink is connected again.
    assert sink._connected is True
    assert client.identified is True

    # Now allow sends; the next one succeeds.
    fail["on"] = False
    await sink._send_snapshot(CaptionSnapshot(committed=["다시"], partial="연결"))
    set_ok = [
        c
        for c in client.calls
        if c.requestType == "SetInputSettings" and "다시" in c.requestData["inputSettings"]["text"]
    ]
    assert set_ok, "send after reconnect did not reach the client"

    await sink.stop()


@pytest.mark.asyncio
async def test_overlapping_send_failures_do_not_launch_parallel_reconnects():
    """Concurrent send failures must coalesce into a single in-flight reconnect."""
    reconnect_attempts = {"count": 0}
    gate = asyncio.Event()

    class SlowReconnectClient(FakeWsClient):
        async def call(self, request: FakeRequest) -> FakeResponse:
            if request.requestType == "SetInputSettings":
                self.calls.append(request)
                raise OSError("down")
            return await super().call(request)

        async def connect(self) -> bool:
            # Count only reconnect-driven connects (start() already connected once).
            if self.connected:
                reconnect_attempts["count"] += 1
                await gate.wait()  # hold the reconnect open so both sends overlap
            self.connected = True
            return True

    client = SlowReconnectClient(inputs=["LiveCaptions"])
    sink = _make_sink(client, source_name="LiveCaptions", debounce_ms=0)
    sink._sleep_fn = _noop_sleep
    sink._password = ""
    await sink.start()

    t1 = asyncio.ensure_future(
        sink._send_snapshot(CaptionSnapshot(committed=["a"], partial=""))
    )
    t2 = asyncio.ensure_future(
        sink._send_snapshot(CaptionSnapshot(committed=["b"], partial=""))
    )
    for _ in range(4):
        await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(t1, t2)

    assert reconnect_attempts["count"] == 1, "parallel reconnect storm"

    await sink.stop()


@pytest.mark.asyncio
async def test_reconnect_respects_max_attempts_and_does_not_raise():
    """If reconnect can never succeed, the send path stays disconnected, respects
    max_connect_attempts, and never raises into the caller."""
    connect_calls = {"count": 0}

    class DeadClient(FakeWsClient):
        async def call(self, request: FakeRequest) -> FakeResponse:
            if request.requestType == "SetInputSettings":
                self.calls.append(request)
                raise OSError("down")
            return await super().call(request)

        async def connect(self) -> bool:
            if self.connected:  # reconnect path
                connect_calls["count"] += 1
                raise OSError("still down")
            self.connected = True
            return True

    client = DeadClient(inputs=["LiveCaptions"])
    sink = _make_sink(client, source_name="LiveCaptions", debounce_ms=0, max_connect_attempts=3)
    sink._sleep_fn = _noop_sleep
    sink._password = ""
    await sink.start()

    # Must not raise even though reconnect fails.
    await sink._send_snapshot(CaptionSnapshot(committed=["x"], partial=""))

    assert sink._connected is False
    assert connect_calls["count"] == 3  # respected max_connect_attempts

    await sink.stop()


@pytest.mark.asyncio
async def test_stop_cancels_in_flight_reconnect_and_does_not_reconnect():
    """B7: stop() during an in-flight reconnect must cancel the reconnect task and
    leave _connected False — the reconnect must NOT re-open the client stop closed."""
    gate = asyncio.Event()
    reconnect_started = asyncio.Event()

    class HangingReconnectClient(FakeWsClient):
        async def call(self, request: FakeRequest) -> FakeResponse:
            if request.requestType == "SetInputSettings":
                self.calls.append(request)
                raise OSError("down")
            return await super().call(request)

        async def connect(self) -> bool:
            if self.connected:  # reconnect path — hang until released
                reconnect_started.set()
                await gate.wait()
            self.connected = True
            return True

    client = HangingReconnectClient(inputs=["LiveCaptions"])
    sink = _make_sink(client, source_name="LiveCaptions", debounce_ms=0)
    sink._sleep_fn = _noop_sleep
    sink._password = ""
    await sink.start()

    # Trigger a send failure -> launches the (hanging) reconnect.
    send = asyncio.ensure_future(
        sink._send_snapshot(CaptionSnapshot(committed=["x"], partial=""))
    )
    await asyncio.wait_for(reconnect_started.wait(), timeout=1.0)
    assert sink._reconnect_task is not None and not sink._reconnect_task.done()

    # stop() while the reconnect is in flight.
    await sink.stop()

    assert sink._reconnect_task is None  # cancelled + cleared
    assert sink._connected is False  # reconnect must NOT have set it True
    assert client.disconnected is True

    gate.set()
    await send  # must not raise into the caller
    # Even after the released connect() body runs, the stopped guard keeps us down.
    assert sink._connected is False


@pytest.mark.asyncio
async def test_truthy_connect_but_failed_identify_is_not_treated_as_connected():
    """B2: connect() returns truthy but wait_until_identified() returns False (wrong
    password / timeout) → the attempt FAILS; the sink must NOT be marked connected."""

    class UnidentifiedClient(FakeWsClient):
        async def connect(self) -> bool:
            self.connected = True
            return True

        async def wait_until_identified(self, timeout: float = 10) -> bool:
            self.identified = False
            return False  # auth rejected / timed out

    client = UnidentifiedClient(inputs=["LiveCaptions"])
    sink = _make_sink(client, source_name="LiveCaptions", max_connect_attempts=2)
    sink._sleep_fn = _noop_sleep
    sink._password = "wrong"

    with pytest.raises(ConnectionError, match="obs-websocket unreachable"):
        await sink.start()

    assert sink._connected is False


@pytest.mark.asyncio
async def test_exactly_one_resend_after_reconnect_for_overlapping_failures():
    """B4: two overlapping send-failures sharing one reconnect must produce EXACTLY
    ONE post-reconnect SetInputSettings (not one per awaiter)."""
    gate = asyncio.Event()
    reconnect_started = asyncio.Event()
    set_calls_after_reconnect = {"count": 0}
    reconnected = {"done": False}

    class CountingClient(FakeWsClient):
        async def call(self, request: FakeRequest) -> FakeResponse:
            if request.requestType == "SetInputSettings":
                self.calls.append(request)
                if reconnected["done"]:
                    set_calls_after_reconnect["count"] += 1
                    return FakeResponse(_ok=True, responseData={})
                # Yield BEFORE failing so both overlapping senders enter the send
                # path (and both fail) before _connected is flipped — otherwise
                # the second sender early-returns and never shares the reconnect.
                await asyncio.sleep(0)
                raise OSError("down")
            return await super().call(request)

        async def connect(self) -> bool:
            if self.connected:  # reconnect path
                reconnect_started.set()
                await gate.wait()
                reconnected["done"] = True
            self.connected = True
            return True

    client = CountingClient(inputs=["LiveCaptions"])
    sink = _make_sink(client, source_name="LiveCaptions", debounce_ms=0)
    sink._sleep_fn = _noop_sleep
    sink._password = ""
    await sink.start()

    t1 = asyncio.ensure_future(
        sink._send_snapshot(CaptionSnapshot(committed=["a"], partial=""))
    )
    t2 = asyncio.ensure_future(
        sink._send_snapshot(CaptionSnapshot(committed=["b"], partial=""))
    )
    await asyncio.wait_for(reconnect_started.wait(), timeout=1.0)
    gate.set()
    await asyncio.gather(t1, t2)

    assert set_calls_after_reconnect["count"] == 1, "duplicate post-reconnect send"

    await sink.stop()


@pytest.mark.asyncio
async def test_newest_snapshot_during_outage_is_the_one_resent():
    """B3: a newer snapshot arriving DURING the reconnect outage must be the one
    re-sent on recovery — not the stale snapshot that triggered the drop."""
    gate = asyncio.Event()
    reconnect_started = asyncio.Event()
    reconnected = {"done": False}

    class WindowClient(FakeWsClient):
        async def call(self, request: FakeRequest) -> FakeResponse:
            if request.requestType == "SetInputSettings":
                self.calls.append(request)
                if not reconnected["done"]:
                    raise OSError("down")
                return FakeResponse(_ok=True, responseData={})
            return await super().call(request)

        async def connect(self) -> bool:
            if self.connected:  # reconnect path
                reconnect_started.set()
                await gate.wait()
                reconnected["done"] = True
            self.connected = True
            return True

    client = WindowClient(inputs=["LiveCaptions"])
    sink = _make_sink(client, source_name="LiveCaptions", debounce_ms=0)
    sink._sleep_fn = _noop_sleep
    sink._password = ""
    await sink.start()

    send = asyncio.ensure_future(
        sink._send_snapshot(CaptionSnapshot(committed=["stale"], partial=""))
    )
    await asyncio.wait_for(reconnect_started.wait(), timeout=1.0)

    # Newer snapshot arrives while still disconnected (outage window).
    sink._on_state_change(CaptionSnapshot(committed=["fresh"], partial="latest"))

    gate.set()
    await send

    set_calls = [c for c in client.calls if c.requestType == "SetInputSettings"]
    last_text = set_calls[-1].requestData["inputSettings"]["text"]
    assert "fresh" in last_text and "latest" in last_text, last_text
    assert "stale" not in last_text, last_text

    await sink.stop()


# ---------------------------------------------------------------------------
# ObsConfig tests
# ---------------------------------------------------------------------------


def test_obs_config_defaults():
    cfg = ObsConfig()
    assert cfg.host == "localhost"
    assert cfg.port == 4455
    assert cfg.source_name == "LiveCaptions"


def test_obs_config_password_from_env(monkeypatch):
    monkeypatch.setenv("OBS_WS_PASSWORD", "mysecret")
    cfg = ObsConfig()
    assert cfg.obs_ws_password == "mysecret"


def test_obs_config_password_none_when_env_missing(monkeypatch):
    monkeypatch.delenv("OBS_WS_PASSWORD", raising=False)
    cfg = ObsConfig()
    assert cfg.obs_ws_password is None


def test_app_config_has_obs_field():
    cfg = AppConfig()
    assert isinstance(cfg.obs, ObsConfig)
    assert cfg.obs.source_name == "LiveCaptions"


def test_redacted_config_includes_obs(monkeypatch):
    """redacted_config should include obs section without exposing password."""
    monkeypatch.setenv("OBS_WS_PASSWORD", "hidden-secret")
    from obs_captions.config import redacted_config

    cfg = AppConfig()
    payload = redacted_config(cfg)
    assert "obs" in payload
    assert "hidden-secret" not in str(payload)


# ---------------------------------------------------------------------------
# Production request builder tests
# ---------------------------------------------------------------------------


def test_production_request_uses_simpleobsws_when_available():
    """_make_production_request returns simpleobsws.Request when simpleobsws is importable."""
    fake_request_cls = MagicMock()
    fake_module = MagicMock()
    fake_module.Request = fake_request_cls

    with patch.dict("sys.modules", {"simpleobsws": fake_module}):
        result = _make_production_request("SetInputSettings", {"inputName": "LiveCaptions"})

    fake_request_cls.assert_called_once_with("SetInputSettings", {"inputName": "LiveCaptions"})
    assert result is fake_request_cls.return_value


def test_production_request_falls_back_to_request_dataclass_without_simpleobsws():
    """_make_production_request falls back to _Request when simpleobsws is not installed."""
    from obs_captions.obs_sink import _Request

    with patch.dict("sys.modules", {"simpleobsws": None}):
        result = _make_production_request("GetInputList", {"inputKind": "text_ft2_source_v2"})

    assert isinstance(result, _Request)
    assert result.requestType == "GetInputList"
    assert result.requestData == {"inputKind": "text_ft2_source_v2"}


# ---------------------------------------------------------------------------
# _build_production_client tests (lines 52-56)
# ---------------------------------------------------------------------------


def test_build_production_client_calls_websocket_client_when_simpleobsws_available():
    """Lines 52-56: _build_production_client delegates to simpleobsws.WebSocketClient."""
    from obs_captions.obs_sink import _build_production_client

    fake_ws_cls = MagicMock()
    fake_module = MagicMock()
    fake_module.WebSocketClient = fake_ws_cls

    with patch.dict("sys.modules", {"simpleobsws": fake_module}):
        result = _build_production_client("ws://localhost:4455", "s3cr3t")

    fake_ws_cls.assert_called_once_with(url="ws://localhost:4455", password="s3cr3t")
    assert result is fake_ws_cls.return_value


def test_build_production_client_raises_runtime_error_without_simpleobsws():
    """Lines 52-55: _build_production_client raises RuntimeError when simpleobsws missing."""
    from obs_captions.obs_sink import _build_production_client

    with patch.dict("sys.modules", {"simpleobsws": None}):
        with pytest.raises(RuntimeError, match="simpleobsws not installed"):
            _build_production_client("ws://localhost:4455", "")


# ---------------------------------------------------------------------------
# Non-injected client path (line 133)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_uses_build_production_client_when_no_client_injected():
    """Line 133: start() calls _build_production_client when no client is injected."""
    fake_client = FakeWsClient(inputs=[])

    obs_config = ObsConfig(host="localhost", port=4455, source_name="LiveCaptions")
    app_config = AppConfig(obs=obs_config)
    state = CaptionState(max_lines=3)
    sink = ObsTextSink(state=state, config=app_config)  # no client injected

    with patch("obs_captions.obs_sink._build_production_client", return_value=fake_client):
        await sink.start()

    assert sink._connected is True
    await sink.stop()


# ---------------------------------------------------------------------------
# _run_reconnect defensive paths (lines 210, 213-214)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_reconnect_handles_connect_retry_raising():
    """Line 210: _run_reconnect's outer except catches if _connect_with_retry raises."""
    client = FakeWsClient(inputs=[])
    sink = _make_sink(client, debounce_ms=0)
    sink._password = ""
    await sink.start()

    # Setting client to None causes _connect_with_retry's assert to raise AssertionError,
    # which propagates to _run_reconnect's outer except (line 210).
    sink._client = None

    # Must not raise; AssertionError is caught and treated as reconnect failure.
    await sink._run_reconnect()
    assert sink._connected is False

    # Restore for clean stop
    sink._client = client
    await sink.stop()


@pytest.mark.asyncio
async def test_run_reconnect_aborts_if_stopped_before_completes():
    """Lines 213-214: _run_reconnect sets connected=False and returns if _stopped is set."""
    client = FakeWsClient(inputs=[])
    sink = _make_sink(client, debounce_ms=0)
    sink._password = ""
    await sink.start()

    # Override _connect_with_retry to set _stopped=True midway (simulates stop() racing).
    async def stopped_retry() -> Exception | None:
        sink._stopped = True
        return None  # "successful" connect but stopped flag is now True

    original_retry = sink._connect_with_retry
    sink._connect_with_retry = stopped_retry  # type: ignore[method-assign]

    await sink._run_reconnect()

    # Despite connect "succeeding", _stopped=True prevents marking connected.
    assert sink._connected is False

    # Restore for clean stop
    sink._connect_with_retry = original_retry  # type: ignore[method-assign]
    sink._stopped = False
    sink._client = client
    await sink.stop()


# ---------------------------------------------------------------------------
# _ensure_source_exists after reconnect raises (lines 230-231)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_reconnect_logs_and_continues_when_ensure_source_raises(caplog):
    """Lines 230-231: _ensure_source_exists raising after reconnect is logged and swallowed."""
    import logging

    call_count = {"n": 0}

    class RaisingEnsureClient(FakeWsClient):
        async def call(self, request: FakeRequest) -> FakeResponse:
            if request.requestType == "GetInputList":
                call_count["n"] += 1
                if call_count["n"] > 1:  # fail only during the post-reconnect call
                    raise RuntimeError("transient GetInputList failure")
            return await super().call(request)

    client = RaisingEnsureClient(inputs=["LiveCaptions"])
    sink = _make_sink(client, debounce_ms=0)
    sink._password = ""
    await sink.start()

    with caplog.at_level(logging.WARNING):
        await sink._run_reconnect()

    assert any("re-ensure source after reconnect failed" in r.getMessage() for r in caplog.records)
    await sink.stop()


# ---------------------------------------------------------------------------
# stop() disconnect exception (lines 253-254)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_swallows_disconnect_exception():
    """Lines 253-254: stop() swallows exceptions raised by client.disconnect()."""

    class DisconnectRaisesClient(FakeWsClient):
        async def disconnect(self) -> None:
            raise RuntimeError("disconnect blew up")

    client = DisconnectRaisesClient(inputs=[])
    sink = _make_sink(client, debounce_ms=0)
    sink._password = ""
    await sink.start()
    await sink.stop()  # must not raise
    assert sink._stopped is True


# ---------------------------------------------------------------------------
# _ensure_source_exists: GetInputList not ok (lines 265-266)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_source_skips_create_when_get_input_list_not_ok():
    """Lines 265-266: when GetInputList returns not-ok, CreateInput is skipped."""

    class NotOkGetInputListClient(FakeWsClient):
        async def call(self, request: FakeRequest) -> FakeResponse:
            if request.requestType == "GetInputList":
                self.calls.append(request)
                return FakeResponse(_ok=False, responseData={})
            return await super().call(request)

    client = NotOkGetInputListClient(inputs=[])
    sink = _make_sink(client, debounce_ms=0)
    sink._password = ""
    await sink.start()
    await sink.stop()

    types = [c.requestType for c in client.calls]
    assert "GetInputList" in types
    assert "CreateInput" not in types


# ---------------------------------------------------------------------------
# _on_state_change early return (line 287)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_state_change_is_no_op_before_start():
    """Line 287: _on_state_change returns early when loop is None (before start)."""
    client = FakeWsClient(inputs=[])
    sink = _make_sink(client, debounce_ms=0)
    # Don't call start() — _loop stays None
    snap = CaptionSnapshot(committed=[], partial="test")
    sink._on_state_change(snap)  # must not raise or schedule anything
    assert sink._pending_snapshot is None


@pytest.mark.asyncio
async def test_on_state_change_is_no_op_after_stop():
    """Line 287: _on_state_change returns early when _stopped is True (after stop)."""
    client = FakeWsClient(inputs=["LiveCaptions"])
    sink = _make_sink(client, debounce_ms=0)
    sink._password = ""
    await sink.start()
    await sink.stop()

    before = sink._pending_snapshot
    snap = CaptionSnapshot(committed=["x"], partial="")
    sink._on_state_change(snap)  # stopped=True → must not update pending_snapshot
    assert sink._pending_snapshot is before


# ---------------------------------------------------------------------------
# _debounce_send CancelledError (lines 326-327)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debounce_send_catches_cancelled_error():
    """Lines 326-327: _debounce_send catches CancelledError and returns silently.

    Cancels AFTER the coroutine has started and is suspended at asyncio.sleep so
    that CancelledError is thrown at the sleep's await (not before the try block).
    """
    client = FakeWsClient(inputs=[])
    sink = _make_sink(client, debounce_ms=0)
    sink._debounce_s = 5.0  # long enough that only a cancel can stop it

    task = asyncio.ensure_future(sink._debounce_send())
    await asyncio.sleep(0)  # one tick: task starts, reaches sleep and suspends

    task.cancel()  # now cancel while task is suspended at asyncio.sleep(5.0)
    for _ in range(4):
        await asyncio.sleep(0)  # deliver CancelledError into the coroutine

    assert task.done()
    assert not task.cancelled()  # task completed normally (CancelledError was caught)


# ---------------------------------------------------------------------------
# _try_set_text with None client (line 347)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_set_text_returns_false_when_client_is_none():
    """Line 347: _try_set_text returns False immediately when _client is None."""
    # Create sink without injecting a client so _client stays None.
    obs_config = ObsConfig(host="localhost", port=4455, source_name="LiveCaptions")
    app_config = AppConfig(obs=obs_config)
    state = CaptionState(max_lines=3)
    sink = ObsTextSink(state=state, config=app_config)  # no client kwarg → _client is None
    result = await sink._try_set_text(CaptionSnapshot(committed=[], partial="x"))
    assert result is False


# ---------------------------------------------------------------------------
# _cancel_and_await non-CancelledError (lines 383-384)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_and_await_swallows_non_cancelled_exception():
    """Lines 383-384: _cancel_and_await swallows exceptions other than CancelledError."""
    from obs_captions.obs_sink import _cancel_and_await

    async def converts_cancel_to_runtime() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise RuntimeError("task converted cancel to RuntimeError")

    task = asyncio.ensure_future(converts_cancel_to_runtime())
    await asyncio.sleep(0)  # let task reach its await
    await _cancel_and_await(task)  # must swallow RuntimeError without raising
    assert task.done()


# ---------------------------------------------------------------------------
# Path B integration: max_chars_per_line wires through to SetInputSettings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_chars_per_line_wraps_text_in_set_input_settings():
    """Path B integration: ObsTextSink respects max_chars_per_line when building display text."""
    from obs_captions.config import OverlayConfig

    client = FakeWsClient(inputs=["LiveCaptions"])
    obs_config = ObsConfig(host="localhost", port=4455, source_name="LiveCaptions")
    app_config = AppConfig(obs=obs_config, overlay=OverlayConfig(max_chars_per_line=5))
    state = CaptionState(max_lines=3)
    sink = ObsTextSink(
        state=state,
        config=app_config,
        client=client,
        debounce_ms=0,
    )
    sink._password = ""

    await sink.start()
    snapshot = CaptionSnapshot(committed=["1234567890"], partial="")
    await sink._send_snapshot(snapshot)
    await sink.stop()

    set_calls = [c for c in client.calls if c.requestType == "SetInputSettings"]
    assert len(set_calls) >= 1
    text = set_calls[-1].requestData["inputSettings"]["text"]
    assert "\n" in text, f"Expected wrapped text with newline, got: {text!r}"
    assert text == "12345\n67890"


# ---------------------------------------------------------------------------
# Medium finding: resp.ok() checks on SetInputSettings / CreateInput
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_set_text_returns_false_when_resp_not_ok(caplog):
    """SetInputSettings returning non-ok (without raising) must yield False + log warning."""
    import logging

    class NonOkSetInputClient(FakeWsClient):
        async def call(self, request: FakeRequest) -> FakeResponse:
            if request.requestType == "SetInputSettings":
                self.calls.append(request)
                return FakeResponse(_ok=False, responseData={})
            return await super().call(request)

    client = NonOkSetInputClient(inputs=["LiveCaptions"])
    sink = _make_sink(client, source_name="LiveCaptions", debounce_ms=0)
    sink._password = ""
    await sink.start()

    with caplog.at_level(logging.WARNING):
        result = await sink._try_set_text(CaptionSnapshot(committed=["hello"], partial=""))

    assert result is False
    assert any(
        "SetInputSettings returned non-ok" in r.getMessage() for r in caplog.records
    )
    await sink.stop()


@pytest.mark.asyncio
async def test_ensure_source_logs_warning_when_create_input_not_ok(caplog):
    """CreateInput returning non-ok (without raising) must log a warning."""
    import logging

    class NonOkCreateInputClient(FakeWsClient):
        async def call(self, request: FakeRequest) -> FakeResponse:
            if request.requestType == "CreateInput":
                self.calls.append(request)
                return FakeResponse(_ok=False, responseData={})
            return await super().call(request)

    # Source absent so CreateInput is called.
    client = NonOkCreateInputClient(inputs=[])
    sink = _make_sink(client, source_name="LiveCaptions", debounce_ms=0)
    sink._password = ""

    with caplog.at_level(logging.WARNING):
        await sink.start()

    await sink.stop()

    create_calls = [c for c in client.calls if c.requestType == "CreateInput"]
    assert len(create_calls) == 1, "CreateInput must still be attempted"
    assert any(
        "CreateInput returned non-ok" in r.getMessage() for r in caplog.records
    )
