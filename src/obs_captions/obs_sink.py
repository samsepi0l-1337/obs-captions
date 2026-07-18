"""Path B sink: obs-websocket v5 → native OBS Text source.

Client: simpleobsws (async-native, no run_in_executor needed). The client
Protocols, request builders, production client factory, and the task-cancel
helper live in ``obs_ws_client`` and are re-exported here so existing import
paths keep working. Request shapes (GetInputList/CreateInput/SetInputSettings)
are built inline in ``_ensure_source_exists`` and ``_try_set_text``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from obs_captions.obs_display import _build_display_text
from obs_captions.obs_ws_client import (
    _Request as _Request,  # re-exported for tests / import compat
)
from obs_captions.obs_ws_client import (
    _WsResponse as _WsResponse,  # re-exported for tests / import compat
)
from obs_captions.obs_ws_client import (
    _build_production_client,
    _cancel_and_await,
    _make_production_request,
    _WsClient,
)

if TYPE_CHECKING:
    from obs_captions.config import AppConfig
    from obs_captions.pipeline import CaptionState

logger = logging.getLogger(__name__)

_TEXT_KIND = "text_ft2_source_v2"
_DEFAULT_SCENE = "Scene"  # fallback scene for CreateInput


# ---------------------------------------------------------------------------
# ObsTextSink
# ---------------------------------------------------------------------------


class ObsTextSink:
    """Subscribes to CaptionState changes and pushes text to an OBS Text source.

    Parameters
    ----------
    state:
        The shared CaptionState whose on_change we hook into.
    config:
        AppConfig (reads config.obs.host/port/source_name + OBS_WS_PASSWORD).
    client:
        Injectable obs-websocket client (for tests). If None, builds from config.
    debounce_ms:
        Coalesce rapid partial updates. 0 = no debounce (tests). Default 120 ms.
    """

    def __init__(
        self,
        *,
        state: CaptionState,  # type: ignore[name-defined]
        config: AppConfig,
        client: _WsClient | None = None,
        debounce_ms: int = 120,
        max_connect_attempts: int = 4,
        sleep_fn: Any | None = None,
    ) -> None:
        self._state = state
        self._config = config
        self._obs = config.obs
        self._debounce_s = debounce_ms / 1000.0
        self._client = client
        self._injected_client = client is not None  # track whether client was injected
        self._password: str = ""  # overridable in tests before start()
        self._connected = False
        self._pending_snapshot: Any | None = None  # CaptionSnapshot at runtime
        self._debounce_task: asyncio.Task[None] | None = None
        self._unsubscribe: Any | None = None  # () -> None returned by state.subscribe
        self._pending_tasks: set[asyncio.Future[None]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._max_connect_attempts = max_connect_attempts
        self._sleep_fn = sleep_fn or asyncio.sleep
        self._url = ""
        self._reconnect_task: asyncio.Task[None] | None = None
        self._stopped = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect to obs-websocket with exponential-backoff retries, ensure source exists, register on_change."""
        self._loop = asyncio.get_running_loop()
        password = self._password or self._obs.obs_ws_password or ""
        url = f"ws://{self._obs.host}:{self._obs.port}"

        if self._injected_client:
            # Inject url/password attributes so tests can assert on them
            self._client.url = url  # type: ignore[union-attr]
            self._client.password = password  # type: ignore[union-attr]
        else:
            self._client = _build_production_client(url, password)

        self._url = url
        last_exc = await self._connect_with_retry()
        if last_exc is not None:
            logger.error(
                "obs-websocket connect failed after %d attempts (%s:%s): %s",
                self._max_connect_attempts,
                self._obs.host,
                self._obs.port,
                last_exc,
            )
            raise ConnectionError(
                f"obs-websocket unreachable at {url} after {self._max_connect_attempts} attempts"
            ) from last_exc

        self._connected = True
        await self._ensure_source_exists()

        # Subscribe (multi-subscriber safe; does not clobber other subscribers)
        self._unsubscribe = self._state.subscribe(self._on_state_change)

    async def _connect_with_retry(self) -> Exception | None:
        """Run the connect+identify loop with exponential backoff.

        Returns the last exception if every attempt failed, else None. Shared by
        start() and the mid-session reconnect path (DRY — single retry loop).
        """
        assert self._client is not None
        last_exc: Exception | None = None
        delay = 0.5
        for attempt in range(1, self._max_connect_attempts + 1):
            try:
                connected = await self._client.connect()
                identified = await self._client.wait_until_identified()
                if connected is False or identified is False:
                    # Falsy connect()/identify (wrong password, timeout) means NOT
                    # connected: fail the attempt so we never mark the sink
                    # connected on an unidentified session.
                    raise ConnectionError("obs-websocket connect/identify returned not-ok")
                return None
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "obs-websocket connect attempt %d/%d failed (%s:%s): %s",
                    attempt,
                    self._max_connect_attempts,
                    self._obs.host,
                    self._obs.port,
                    exc,
                )
                if attempt < self._max_connect_attempts:
                    await self._sleep_fn(delay)
                    delay = min(delay * 2, 30.0)
        return last_exc

    async def _reconnect(self) -> bool:
        """Re-run the connect loop after a mid-session drop.

        A single in-flight reconnect is shared by all concurrent callers (no
        parallel reconnect storms). Returns True on success; on failure stays
        disconnected and logs (never raises into the send path).
        """
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.ensure_future(self._run_reconnect())
        try:
            await self._reconnect_task
        except asyncio.CancelledError:
            return False  # stop() cancelled the reconnect; never raise into send
        return self._connected

    async def _run_reconnect(self) -> None:
        # Never raise into the caller: a raising injected sleep_fn (backoff) or a
        # client error during reconnect must not escape the realtime send path.
        try:
            last_exc = await self._connect_with_retry()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        if self._stopped:
            # stop() began mid-reconnect — do not re-open behind stop()'s back.
            self._connected = False
            return
        if last_exc is not None:
            logger.error(
                "obs-websocket reconnect failed after %d attempts (%s:%s): %s",
                self._max_connect_attempts,
                self._obs.host,
                self._obs.port,
                last_exc,
            )
            self._connected = False
            return
        self._connected = True
        # Source may have been deleted during the outage; re-ensure so a recovered
        # connection doesn't silently lose every future caption.
        try:
            await self._ensure_source_exists()
        except Exception as exc:  # noqa: BLE001
            logger.warning("re-ensure source after reconnect failed: %s", exc)
        # EXACTLY ONE re-send of the latest snapshot per reconnect (here, not in
        # each awaiter), so N coalesced failed senders produce ONE re-send.
        if self._pending_snapshot is not None and not self._stopped:
            await self._try_set_text(self._pending_snapshot)

    async def stop(self) -> None:
        """Cancel debounce + in-flight reconnect, disconnect, unregister callback."""
        self._stopped = True
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        # Cancel both background tasks. The in-flight reconnect must not survive
        # stop() and re-open the client we are about to close.
        await _cancel_and_await(self._debounce_task)
        self._debounce_task = None
        await _cancel_and_await(self._reconnect_task)
        self._reconnect_task = None
        self._connected = False
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_source_exists(self) -> None:
        """GetInputList; CreateInput if source not present."""
        assert self._client is not None
        resp = await self._client.call(_make_production_request("GetInputList", {"inputKind": _TEXT_KIND}))
        if not resp.ok():
            logger.warning("GetInputList failed, skipping source check")
            return

        existing = {inp["inputName"] for inp in resp.responseData.get("inputs", [])}
        if self._obs.source_name not in existing:
            logger.info("Creating OBS text source: %s", self._obs.source_name)
            create_resp = await self._client.call(
                _make_production_request(
                    "CreateInput",
                    {
                        "sceneName": _DEFAULT_SCENE,
                        "inputName": self._obs.source_name,
                        "inputKind": _TEXT_KIND,
                        "inputSettings": {"text": ""},
                        "sceneItemEnabled": True,
                    },
                )
            )
            if not create_resp.ok():
                logger.warning(
                    "CreateInput returned non-ok status: %s; source may not appear in OBS",
                    create_resp.requestStatus,
                )

    def _on_state_change(self, snapshot: Any) -> None:
        """Called by CaptionState. Schedule debounced push (loop-safe)."""
        if self._loop is None or self._stopped:
            return
        if not self._connected:
            # During a reconnect outage we cannot send, but we MUST still capture
            # the most-recent snapshot so the post-reconnect re-send reflects the
            # latest caption, not a stale pre-outage value.
            self._pending_snapshot = snapshot
            return
        self._loop.call_soon_threadsafe(self._schedule_update, snapshot)

    def _schedule_update(self, snapshot: Any) -> None:
        """Must be called from the event loop thread."""
        self._pending_snapshot = snapshot
        if self._debounce_s <= 0:
            self._track_task(asyncio.ensure_future(self._send_snapshot(snapshot)))
            return
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = asyncio.ensure_future(self._debounce_send())
        # Route through the same exception-logging done-callback so a raise from
        # _send_snapshot during the debounce window is logged when it happens —
        # not silently swallowed until stop(). CancelledError stays unlogged.
        self._debounce_task.add_done_callback(self._on_task_done)

    def _track_task(self, task: asyncio.Future[None]) -> None:
        """Retain a fire-and-forget task ref and log any exception it raises."""
        self._pending_tasks.add(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Future[None]) -> None:
        # Inspect/log the exception FIRST, then discard from the retention set.
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                logger.error("obs sink background task failed: %s", exc, exc_info=exc)
        self._pending_tasks.discard(task)

    async def _debounce_send(self) -> None:
        try:
            await asyncio.sleep(self._debounce_s)
        except asyncio.CancelledError:
            return
        if self._pending_snapshot is not None:
            await self._send_snapshot(self._pending_snapshot)

    async def _send_snapshot(self, snapshot: Any) -> None:
        if not self._connected or self._client is None:
            return
        # Latest pending snapshot for the single re-send owned by _run_reconnect.
        self._pending_snapshot = snapshot
        if await self._try_set_text(snapshot):
            return
        # Send failed mid-session: reconnect once. _run_reconnect owns the single
        # re-send, so concurrent failed senders awaiting it do not each duplicate.
        self._connected = False
        logger.warning("SetInputSettings failed; attempting reconnect")
        await self._reconnect()

    async def _try_set_text(self, snapshot: Any) -> bool:
        """Push one SetInputSettings. Returns True on success, False on failure."""
        if self._client is None:
            return False
        text = _build_display_text(snapshot, max_chars=self._config.overlay.max_chars_per_line)
        try:
            resp = await self._client.call(
                _make_production_request(
                    "SetInputSettings",
                    {
                        "inputName": self._obs.source_name,
                        "inputSettings": {"text": text},
                    },
                )
            )
            if not resp.ok():
                logger.warning(
                    "SetInputSettings returned non-ok status: %s", resp.requestStatus
                )
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("SetInputSettings failed: %s", exc)
            return False
