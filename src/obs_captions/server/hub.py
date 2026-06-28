from __future__ import annotations

import asyncio
from typing import Any

from starlette.websockets import WebSocket


DEFAULT_CAPTION_MESSAGE = {"type": "caption", "partial": "", "committed": []}


class Hub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._last_message: dict[str, Any] = dict(DEFAULT_CAPTION_MESSAGE)

    @property
    def last_snapshot(self) -> dict[str, Any]:
        return _copy_message(self._last_message)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        await ws.send_json(self.last_snapshot)

    async def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, message: dict[str, Any]) -> None:
        next_message = _copy_message(message)
        if next_message == self._last_message:
            return

        self._last_message = next_message
        clients = list(self._clients)
        results = await asyncio.gather(
            *(client.send_json(self.last_snapshot) for client in clients),
            return_exceptions=True,
        )
        for client, result in zip(clients, results):
            if isinstance(result, BaseException):
                self._clients.discard(client)


def _copy_message(message: dict[str, Any]) -> dict[str, Any]:
    copied = dict(message)
    copied["committed"] = list(copied.get("committed", []))
    return copied
