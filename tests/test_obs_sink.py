"""Tests for ObsTextSink — all mock-based, no real OBS required."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
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
) -> ObsTextSink:
    obs_config = ObsConfig(host="localhost", port=4455, source_name=source_name)
    app_config = AppConfig(obs=obs_config)
    state = CaptionState(max_lines=3)
    return ObsTextSink(
        state=state,
        config=app_config,
        client=client,
        debounce_ms=debounce_ms,
        max_connect_attempts=max_connect_attempts,
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
