from __future__ import annotations

import asyncio
import contextlib
from abc import abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from obs_captions.stt.base import STTBackend, Transcript


class WSTransport(Protocol):
    """Minimal duck-typed WebSocket transport (matches ``websockets`` client)."""

    async def send(self, message: str | bytes) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


ConnectFn = Callable[[str, dict[str, str]], Awaitable[WSTransport]]


@dataclass(frozen=True)
class ConnectInfo:
    """URL + headers used to open the websocket."""

    url: str
    headers: dict[str, str]


@dataclass(frozen=True)
class ParsedEvent:
    """Result of parsing one inbound provider message.

    ``text`` is interpreted by :class:`StreamingBackend` according to ``kind``:
    a ``"partial"`` carries the (possibly delta) hypothesis, a ``"final"``
    carries a committed segment. ``None`` kind means "ignore this message".
    """

    kind: str | None
    text: str = ""
    is_delta: bool = False


class StreamingBackend(STTBackend):
    """Base for realtime websocket STT providers.

    Lifecycle: ``start_stream`` opens the websocket (via the injectable
    ``connect_fn``), sends any provider setup frames, and launches a background
    receive loop. ``feed_audio`` encodes PCM16 and sends it. ``stop_stream``
    closes the socket and cancels the loop. The receive loop reconnects with
    exponential backoff when the connection drops while the stream is active.

    ``on_partial`` always receives the FULL current hypothesis. Providers that
    emit deltas set ``ParsedEvent.is_delta=True`` so the base accumulates them.
    """

    def __init__(
        self,
        *,
        connect_fn: ConnectFn | None = None,
        max_reconnects: int = 5,
        backoff_base: float = 0.5,
        backoff_max: float = 8.0,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._connect_fn = connect_fn
        self._max_reconnects = max_reconnects
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._sleep_fn = sleep_fn or asyncio.sleep
        self._ws: WSTransport | None = None
        self._recv_task: asyncio.Task[None] | None = None
        self._running = False
        self._partial_accum = ""
        self._send_lock = asyncio.Lock()

    # ------------------------------------------------------------------ hooks
    @abstractmethod
    def build_connect(self) -> ConnectInfo:
        """Return the websocket URL and auth headers."""

    @abstractmethod
    def encode_audio(self, pcm16: bytes) -> str | bytes:
        """Encode normalized 16 kHz PCM16 into one outbound message."""

    @abstractmethod
    def parse_event(self, message: str | bytes) -> ParsedEvent:
        """Parse one inbound provider message into a :class:`ParsedEvent`."""

    def initial_messages(self) -> list[str | bytes]:
        """Provider setup frames sent right after connect. Default: none."""
        return []

    # -------------------------------------------------------------- lifecycle
    async def start_stream(self) -> None:
        if self._running:
            return
        self._running = True
        self._partial_accum = ""
        await self._open()
        self._recv_task = asyncio.create_task(self._receive_loop())

    async def feed_audio(self, pcm16: bytes) -> None:
        if not self._running or not pcm16:
            return
        message = self.encode_audio(pcm16)
        await self._send(message)

    async def flush(self) -> None:
        """Streaming providers finalize server-side; nothing to flush here."""
        return None

    async def stop_stream(self) -> None:
        self._running = False
        if self._recv_task is not None:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._recv_task
            self._recv_task = None
        await self._close_ws()
        self._partial_accum = ""

    # ------------------------------------------------------------- internals
    async def _open(self) -> None:
        if self._connect_fn is None:
            self._connect_fn = await self._default_connect_fn()
        info = self.build_connect()
        self._ws = await self._connect_fn(info.url, info.headers)
        for message in self.initial_messages():
            await self._send(message)

    async def _send(self, message: str | bytes) -> None:
        ws = self._ws
        if ws is None:
            return
        async with self._send_lock:
            await ws.send(message)

    async def _close_ws(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    async def _receive_loop(self) -> None:
        attempts = 0
        while self._running:
            ws = self._ws
            if ws is None:
                if not await self._reconnect(attempts):
                    return
                attempts += 1
                continue
            try:
                message = await ws.recv()
            except asyncio.CancelledError:
                raise
            except Exception:
                if not self._running:
                    return
                await self._close_ws()
                if not await self._reconnect(attempts):
                    return
                attempts += 1
                continue
            attempts = 0
            self._dispatch(message)

    async def _reconnect(self, attempts: int) -> bool:
        if attempts >= self._max_reconnects:
            return False
        delay = min(self._backoff_base * (2**attempts), self._backoff_max)
        await self._sleep_fn(delay)
        if not self._running:
            return False
        try:
            await self._open()
        except Exception:
            return self._running
        return True

    def _dispatch(self, message: str | bytes) -> None:
        event = self.parse_event(message)
        if event.kind == "partial":
            if event.is_delta:
                self._partial_accum += event.text
                text = self._partial_accum
            else:
                text = event.text
                self._partial_accum = text
            self.on_partial(Transcript(text=text, is_final=False, lang=self.language))
        elif event.kind == "final":
            self._partial_accum = ""
            text = event.text.strip()
            if text:
                self.on_final(Transcript(text=text, is_final=True, lang=self.language))

    async def _default_connect_fn(self) -> ConnectFn:
        import websockets

        async def _connect(url: str, headers: dict[str, str]) -> WSTransport:
            return await websockets.connect(url, additional_headers=headers)  # type: ignore[return-value]

        return _connect


def header_dict(**pairs: str | None) -> dict[str, str]:
    """Drop ``None`` values so optional headers are omitted cleanly."""
    return {key: value for key, value in pairs.items() if value is not None}
