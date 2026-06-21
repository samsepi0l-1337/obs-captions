from __future__ import annotations

import asyncio


class FakeWS:
    """In-memory websocket transport for StreamingBackend tests.

    Records every sent message in ``sent``. Inbound provider messages are
    queued via ``push`` (or seeded in the constructor) and returned by
    ``recv`` in order; once drained ``recv`` blocks until more are pushed or
    the socket is closed.
    """

    def __init__(self, inbound: list[str | bytes] | None = None) -> None:
        self.sent: list[str | bytes] = []
        self.closed = False
        self._queue: asyncio.Queue[str | bytes] = asyncio.Queue()
        for message in inbound or []:
            self._queue.put_nowait(message)

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        return await self._queue.get()

    async def close(self) -> None:
        self.closed = True

    def push(self, message: str | bytes) -> None:
        self._queue.put_nowait(message)


def fake_connect(ws: FakeWS):
    """Build an injectable connect_fn that records the url + headers used."""
    captured: dict[str, object] = {}

    async def _connect(url: str, headers: dict[str, str]) -> FakeWS:
        captured["url"] = url
        captured["headers"] = headers
        return ws

    return _connect, captured


async def wait_for(predicate, timeout: float = 1.0) -> None:
    """Poll ``predicate`` until true or raise TimeoutError."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise TimeoutError("condition not met within timeout")
