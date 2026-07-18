"""obs-websocket v5 client boundary for the Path B sink.

Holds the injectable client/response Protocols, the duck-typed ``_Request``
fallback, the production factories that lazily import ``simpleobsws``, and the
task-cancel helper. Extracted from ``obs_sink`` so each module stays under the
line-count contract; ``obs_sink`` re-exports these names for existing import
paths (tests, ``obs_hotkey``).

Doc source: https://context7.com/irltoolkit/simpleobsws/llms.txt
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# Minimal Request dataclass — compatible with simpleobsws.Request; used so tests
# never need simpleobsws installed.
@dataclass
class _Request:
    requestType: str
    requestData: dict[str, Any] = field(default_factory=dict)


# Protocols — let tests inject a fake without importing simpleobsws.
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


def _build_production_client(url: str, password: str) -> _WsClient:
    # Production client factory — only called when no client is injected.
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


async def _cancel_and_await(task: asyncio.Task[None] | None) -> None:
    """Cancel task; swallow CancelledError/faults (faults already logged by done-cb)."""
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001
        pass
