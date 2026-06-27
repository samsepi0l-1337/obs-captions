"""OBS hotkey listener: routes InputMuteStateChanged events to CaptionController.

Uses a dedicated obs-websocket v5 connection subscribing only to Inputs events
(EventSubscription bitmask = 1 << 3). Two sentinel "Audio Input Capture" sources
in OBS (_CaptionPause and _CaptionClear by default) allow pause/resume and
caption-clear via OBS hotkeys — no obs-websocket->client hotkey event needed.

Design rationale: obs-websocket v5 has no OBS→client hotkey event. The
established community pattern is a sentinel audio input whose mute state OBS
can bind to a hotkey and whose mute-state change is emitted as
InputMuteStateChanged. The sentinel source needs no scene presence (lives in the
Audio Mixer only).

Client is injectable so tests run with a fake — no network required.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from obs_captions.config import AppConfig
    from obs_captions.pipeline import CaptionState

logger = logging.getLogger(__name__)

# obs-websocket v5 EventSubscription bitmask for the "Inputs" category.
# Mute/unmute events (InputMuteStateChanged) live in this subscription group.
EVENT_SUBSCRIPTION_INPUTS: int = 1 << 3  # = 8


# ---------------------------------------------------------------------------
# Protocols — let tests inject a fake without importing simpleobsws.
# ---------------------------------------------------------------------------


@runtime_checkable
class _HotkeyWsResponse(Protocol):
    def ok(self) -> bool: ...


@runtime_checkable
class _HotkeyWsClient(Protocol):
    async def connect(self) -> bool: ...
    async def wait_until_identified(self, timeout: float = 10) -> bool: ...
    async def call(self, request: Any) -> _HotkeyWsResponse: ...
    async def disconnect(self) -> None: ...
    def register_event_callback(self, callback: Any, event_type: str | None = None) -> None: ...
    def deregister_event_callback(self, callback: Any) -> None: ...


# ---------------------------------------------------------------------------
# Production client factory
# ---------------------------------------------------------------------------


def _build_production_hotkey_client(url: str, password: str) -> _HotkeyWsClient:
    """Build a simpleobsws client subscribed only to Inputs events."""
    try:
        import simpleobsws  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError("simpleobsws not installed. Run: uv sync --extra obs") from exc
    params = simpleobsws.IdentificationParameters(eventSubscriptions=EVENT_SUBSCRIPTION_INPUTS)
    return simpleobsws.WebSocketClient(  # type: ignore[return-value]
        url=url, password=password, identification_parameters=params
    )


def _make_hotkey_request(request_type: str, data: dict[str, Any] | None = None) -> Any:
    """Build a request using simpleobsws.Request when available, else the _Request dataclass."""
    from obs_captions.obs_sink import _make_production_request

    return _make_production_request(request_type, data)


# ---------------------------------------------------------------------------
# CaptionController
# ---------------------------------------------------------------------------


class CaptionController:
    """Shared mutable state between ObsHotkeyListener and the audio capture loop.

    Holds the paused flag and a reference to CaptionState so the listener can
    pause/resume audio gating and clear the caption overlay.
    """

    def __init__(self, state: CaptionState) -> None:
        self._state = state
        self._paused: bool = False

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        """Set the paused flag. True = drop audio frames; False = process normally."""
        self._paused = paused

    def clear(self) -> None:
        """Clear all committed lines and partial text from the caption overlay."""
        self._state.clear()


# ---------------------------------------------------------------------------
# ObsHotkeyListener
# ---------------------------------------------------------------------------


class ObsHotkeyListener:
    """Listens for InputMuteStateChanged on a dedicated obs-websocket v5 connection.

    Pause sentinel (_CaptionPause):
        Muting the sentinel → controller.set_paused(True)   (drops audio frames).
        Unmuting          → controller.set_paused(False)  (resumes processing).
        OBS setup: bind the SAME key to both "Mute _CaptionPause" and
        "Unmute _CaptionPause" so one key press toggles.

    Clear sentinel (_CaptionClear):
        Muting the sentinel → controller.clear() + SetInputMute(False) to re-arm.
        Unmuting is ignored (the re-arm does the unmute; OBS may fire it too).
        OBS setup: bind a key to "Mute _CaptionClear" ONLY.

    Parameters
    ----------
    config:
        AppConfig — reads obs.host, obs.port, obs.hotkey.* settings.
    controller:
        Shared CaptionController (paused flag + CaptionState ref).
    client:
        Injectable obs-websocket client (for tests). If None, builds from config.
    sleep_fn:
        asyncio.sleep replacement (injectable to skip real delays in tests).
    max_connect_attempts:
        Retry count for connect/reconnect (same semantics as ObsTextSink).
    ping_interval:
        Seconds between keepalive pings to detect disconnects. Set 0 in tests.
    """

    def __init__(
        self,
        *,
        config: AppConfig,
        controller: CaptionController,
        client: _HotkeyWsClient | None = None,
        sleep_fn: Any | None = None,
        max_connect_attempts: int = 4,
        ping_interval: float = 5.0,
    ) -> None:
        self._config = config
        self._hotkey = config.obs.hotkey
        self._controller = controller
        self._client = client
        self._injected_client = client is not None
        self._sleep_fn = sleep_fn or asyncio.sleep
        self._max_connect_attempts = max_connect_attempts
        self._ping_interval = ping_interval
        self._stopped = False
        self._listener_task: asyncio.Task[None] | None = None
        # Store as an instance variable so identity-based unregister works.
        # (Python creates a new bound method object on each attribute access,
        # so `self._on_event is self._on_event` would be False without storing.)
        self._callback = self._on_event
        # Strong reference to the in-flight re-arm task so it cannot be GC'd
        # mid-execution (CPython task GC documented in asyncio.ensure_future).
        self._reset_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect with retry, register the event callback, start the keepalive loop.

        Failure to connect is a soft failure: a warning is logged and the
        listener simply does not start (hotkeys unavailable but app continues).
        """
        url = f"ws://{self._config.obs.host}:{self._config.obs.port}"
        password = self._config.obs.obs_ws_password or ""

        if self._injected_client:
            self._client.url = url  # type: ignore[union-attr]
            self._client.password = password  # type: ignore[union-attr]
        else:
            self._client = _build_production_hotkey_client(url, password)  # pragma: no cover

        exc = await self._connect_with_retry()
        if exc is not None:
            logger.warning(
                "obs-websocket hotkey: initial connect failed, hotkeys unavailable (%s:%s): %s",
                self._config.obs.host,
                self._config.obs.port,
                exc,
            )
            return

        assert self._client is not None
        self._client.register_event_callback(self._callback, "InputMuteStateChanged")
        self._listener_task = asyncio.create_task(self._run_keepalive())

    async def stop(self) -> None:
        """Cancel the keepalive task and disconnect the client.

        Drains any in-flight _reset_clear_sentinel task before marking stopped,
        so the clear sentinel is always re-armed even when stop() races with a
        clear hotkey event fired just before teardown.
        """
        if self._reset_task is not None and not self._reset_task.done():
            with contextlib.suppress(Exception):
                await self._reset_task
        self._stopped = True
        if self._listener_task is not None and not self._listener_task.done():
            self._listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener_task
            self._listener_task = None
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.disconnect()

    # ------------------------------------------------------------------
    # Connect helpers (mirrors ObsTextSink._connect_with_retry)
    # ------------------------------------------------------------------

    async def _connect_with_retry(self) -> Exception | None:
        """Connect + identify with exponential backoff.

        Returns None on success, or the last exception if all attempts failed.
        """
        assert self._client is not None
        delay = 0.5
        last_exc: Exception | None = None
        for attempt in range(1, self._max_connect_attempts + 1):
            try:
                connected = await self._client.connect()
                identified = await self._client.wait_until_identified()
                if connected is False or identified is False:
                    raise ConnectionError("obs-websocket connect/identify returned not-ok")
                return None
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "obs-websocket hotkey connect attempt %d/%d failed: %s",
                    attempt,
                    self._max_connect_attempts,
                    exc,
                )
                if attempt < self._max_connect_attempts:
                    await self._sleep_fn(delay)
                    delay = min(delay * 2, 30.0)
        return last_exc

    # ------------------------------------------------------------------
    # Keepalive + reconnect loop
    # ------------------------------------------------------------------

    async def _run_keepalive(self) -> None:
        """Ping periodically; on disconnect unregister, reconnect, re-subscribe."""
        while not self._stopped:
            disconnected = await self._ping_until_disconnect()
            if self._stopped or not disconnected:
                return

            logger.warning("obs-websocket hotkey: connection lost; reconnecting...")
            assert self._client is not None
            with contextlib.suppress(Exception):
                self._client.deregister_event_callback(self._callback)

            exc = await self._connect_with_retry()
            if exc is not None:
                logger.error("obs-websocket hotkey: reconnect failed: %s", exc)
                return

            self._client.register_event_callback(self._callback, "InputMuteStateChanged")

    async def _ping_until_disconnect(self) -> bool:
        """Sleep and ping until a disconnect is detected or stop() is called.

        Returns True  if a disconnect was detected (caller should reconnect).
        Returns False if stopped cleanly (caller should exit).
        """
        assert self._client is not None
        while not self._stopped:
            try:
                await self._sleep_fn(self._ping_interval)
                if self._stopped:
                    return False  # pragma: no cover  # stopped mid-sleep (real network path)
                resp = await self._client.call(_make_hotkey_request("GetVersion", {}))
                if not resp.ok():
                    return True
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                return True
        return False

    # ------------------------------------------------------------------
    # Event handler
    # ------------------------------------------------------------------

    async def _on_event(self, event_data: Any) -> None:
        """Handle InputMuteStateChanged for the two sentinel sources."""
        try:
            input_name: str = event_data["inputName"]
            input_muted: bool = event_data["inputMuted"]
        except (KeyError, TypeError):
            return

        hotkey = self._hotkey
        if input_name == hotkey.pause_input:
            self._controller.set_paused(input_muted)
        elif input_name == hotkey.clear_input and input_muted:
            try:
                self._controller.clear()
            finally:
                # Auto-unmute the sentinel so the next key press re-arms it.
                # Scheduled unconditionally (try/finally) so a subscriber that
                # raises inside clear() cannot prevent re-arming the sentinel.
                # Strong ref stored to prevent GC before the coroutine completes.
                self._reset_task = asyncio.create_task(self._reset_clear_sentinel())

    async def _reset_clear_sentinel(self) -> None:
        """Issue SetInputMute(inputMuted=False) to re-arm the clear sentinel."""
        if self._client is None or self._stopped:
            return
        try:
            resp = await self._client.call(
                _make_hotkey_request(
                    "SetInputMute",
                    {"inputName": self._hotkey.clear_input, "inputMuted": False},
                )
            )
            if not resp.ok():
                logger.warning(
                    "obs-websocket hotkey: SetInputMute reset returned not-ok;"
                    " clear sentinel may not re-arm"
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("obs-websocket hotkey: reset clear sentinel failed: %s", exc)
