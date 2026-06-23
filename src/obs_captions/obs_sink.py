"""Path B sink: obs-websocket v5 → native OBS Text source.

Client: simpleobsws (async-native, no run_in_executor needed).
Doc source: https://context7.com/irltoolkit/simpleobsws/llms.txt

Confirmed request shapes (obs-websocket v5):
  GetInputList     → requestData={'inputKind': 'text_ft2_source_v2'}
                     response:   {'inputs': [{'inputName': ..., 'inputKind': ...}]}
  CreateInput      → requestData={'sceneName': ..., 'inputName': ...,
                                  'inputKind': 'text_ft2_source_v2',
                                  'inputSettings': {'text': ''},
                                  'sceneItemEnabled': True}
  SetInputSettings → requestData={'inputName': ..., 'inputSettings': {'text': '...'}}
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from obs_captions.config import AppConfig
    from obs_captions.pipeline import CaptionState

logger = logging.getLogger(__name__)

_TEXT_KIND = "text_ft2_source_v2"
_DEFAULT_SCENE = "Scene"  # fallback scene for CreateInput


# ---------------------------------------------------------------------------
# Minimal Request dataclass — compatible with simpleobsws.Request interface.
# Used internally so tests never need simpleobsws installed.
# ---------------------------------------------------------------------------


@dataclass
class _Request:
    requestType: str
    requestData: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocol — lets tests inject a fake without importing simpleobsws
# ---------------------------------------------------------------------------


@runtime_checkable
class _WsResponse(Protocol):
    def ok(self) -> bool: ...

    responseData: dict[str, Any]


@runtime_checkable
class _WsClient(Protocol):
    async def connect(self) -> bool: ...
    async def wait_until_identified(self, timeout: float = 10) -> bool: ...
    async def call(self, request: Any) -> _WsResponse: ...
    async def disconnect(self) -> None: ...


# ---------------------------------------------------------------------------
# Production client factory — only called when no client is injected
# ---------------------------------------------------------------------------


def _build_production_client(url: str, password: str) -> _WsClient:
    try:
        import simpleobsws  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError("simpleobsws not installed. Run: uv sync --extra obs") from exc
    return simpleobsws.WebSocketClient(url=url, password=password)  # type: ignore[return-value]


def _make_production_request(request_type: str, data: dict[str, Any] | None = None) -> Any:
    """Build a request using simpleobsws.Request when available, else _Request (duck-typed)."""
    try:
        import simpleobsws  # type: ignore[import-untyped]

        return simpleobsws.Request(request_type, data or {})
    except ImportError:
        return _Request(requestType=request_type, requestData=data or {})


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

        last_exc: Exception | None = None
        delay = 0.5
        for attempt in range(1, self._max_connect_attempts + 1):
            try:
                await self._client.connect()  # type: ignore[union-attr]
                await self._client.wait_until_identified()  # type: ignore[union-attr]
                last_exc = None
                break
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

    async def stop(self) -> None:
        """Cancel debounce task, disconnect, unregister callback."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._debounce_task is not None:
            self._debounce_task.cancel()
            try:
                await self._debounce_task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                # Already surfaced by the _on_task_done done-callback; don't let
                # a faulted debounce send propagate out of stop().
                pass
            self._debounce_task = None
        if self._client is not None and self._connected:
            try:
                await self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self._connected = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_source_exists(self) -> None:
        """GetInputList; CreateInput if source not present."""
        assert self._client is not None
        resp = await self._client.call(_make_req("GetInputList", {"inputKind": _TEXT_KIND}))
        if not resp.ok():
            logger.warning("GetInputList failed, skipping source check")
            return

        existing = {inp["inputName"] for inp in resp.responseData.get("inputs", [])}
        if self._obs.source_name not in existing:
            logger.info("Creating OBS text source: %s", self._obs.source_name)
            await self._client.call(
                _make_req(
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

    def _on_state_change(self, snapshot: Any) -> None:
        """Called by CaptionState. Schedule debounced push (loop-safe)."""
        if self._loop is None or not self._connected:
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
        text = _build_display_text(snapshot)
        try:
            await self._client.call(
                _make_req(
                    "SetInputSettings",
                    {
                        "inputName": self._obs.source_name,
                        "inputSettings": {"text": text},
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("SetInputSettings failed: %s", exc)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _make_req(request_type: str, data: dict[str, Any] | None = None) -> Any:
    """Build a request object: simpleobsws.Request when available, else _Request (duck-typed)."""
    return _make_production_request(request_type, data)


def _build_display_text(snapshot: Any) -> str:
    """Join committed lines + partial tail into display string."""
    parts = list(snapshot.committed)
    if snapshot.partial:
        parts.append(snapshot.partial)
    return "\n".join(parts)
