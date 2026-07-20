from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from obs_captions.ipc.framing import Control, StatusCode

CONTROL_START = 1
CONTROL_STOP = 2
CONTROL_FLUSH = 3
CONTROL_RECONFIGURE = 4
CONTROL_COMMANDS = (CONTROL_START, CONTROL_STOP, CONTROL_FLUSH, CONTROL_RECONFIGURE)


@dataclass
class _QueuedControl:
    command: int
    seq: int
    arg: str


class ControlDispatcher:
    """Owns the control-command queue state machine (slots / inflight / last_seq).

    Supersede / ack / inflight state transitions live here. Session execution
    (start / stop / flush / reconfigure) stays in ``SidecarRuntime`` and is
    reached through the injected ``run_control`` callback; the live session flags
    read during a drain are injected as probes so the read points and drain
    ordering match the original inline code exactly.
    """

    def __init__(
        self,
        *,
        enqueue_status: Callable[[StatusCode, int, str], None],
        run_control: Callable[[_QueuedControl], Awaitable[None]],
        has_session_context: Callable[[], bool],
        is_session_active: Callable[[], bool],
        has_backend: Callable[[], bool],
    ) -> None:
        self._enqueue_status = enqueue_status
        self._run_control = run_control
        self._has_session_context = has_session_context
        self._is_session_active = is_session_active
        self._has_backend = has_backend

        self.slots: dict[int, _QueuedControl | None] = {
            CONTROL_START: None,
            CONTROL_STOP: None,
            CONTROL_FLUSH: None,
            CONTROL_RECONFIGURE: None,
        }
        self.inflight: dict[int, int | None] = {
            CONTROL_START: None,
            CONTROL_STOP: None,
            CONTROL_FLUSH: None,
            CONTROL_RECONFIGURE: None,
        }
        self.last_seq: dict[int, int] = {
            CONTROL_START: -1,
            CONTROL_STOP: -1,
            CONTROL_FLUSH: -1,
            CONTROL_RECONFIGURE: -1,
        }
        self.lock = threading.Lock()

    def enqueue(self, control: Control) -> None:
        if control.command not in CONTROL_COMMANDS:
            self._enqueue_status(StatusCode.RUNTIME_ERROR, control.seq, "unknown control command")
            return

        with self.lock:
            if not self._has_session_context():
                self._enqueue_status(StatusCode.NO_SESSION, control.seq, "no session")
                return

            if self.inflight[control.command] is not None:
                if self.inflight[control.command] == control.seq:
                    self._enqueue_status(StatusCode.OK, control.seq, "ack")
                    return
                self._enqueue_status(StatusCode.CANCELLED, control.seq, "superseded")
                return

            last_seq = self.last_seq[control.command]
            if control.seq == last_seq:
                self._enqueue_status(StatusCode.OK, control.seq, "ack")
                return
            if control.seq < last_seq:
                self._enqueue_status(StatusCode.CANCELLED, control.seq, "superseded")
                return

            prior = self.slots[control.command]
            if prior is not None:
                self._enqueue_status(StatusCode.CANCELLED, prior.seq, "superseded")
            self.slots[control.command] = _QueuedControl(
                command=control.command,
                seq=control.seq,
                arg=control.arg,
            )

    async def drain_once(self) -> None:
        for command in CONTROL_COMMANDS:
            control = None
            with self.lock:
                if self.inflight[command] is not None:
                    continue
                control = self.slots[command]
                if control is None:
                    continue
                self.slots[command] = None
                self.inflight[command] = control.seq

            if control is None:
                continue

            if (
                control.command in (CONTROL_FLUSH, CONTROL_RECONFIGURE)
                and not self._has_backend()
            ):
                if self._has_session_context() and not self._is_session_active():
                    with self.lock:
                        self.slots[command] = control
                        self.inflight[command] = None
                    await asyncio.sleep(0.001)
                    continue

                with self.lock:
                    self.inflight[command] = None

                self._enqueue_status(StatusCode.NO_SESSION, control.seq, "no session")
                continue

            try:
                await self._run_control(control)
            except Exception as exc:  # noqa: BLE001
                self._enqueue_status(StatusCode.RUNTIME_ERROR, control.seq, str(exc))
            finally:
                with self.lock:
                    self.last_seq[command] = control.seq
                    self.inflight[command] = None

    def clear_pending(self) -> None:
        for command in CONTROL_COMMANDS:
            with self.lock:
                pending = self.slots.get(command)
                self.slots[command] = None
                self.inflight[command] = None
            if pending is not None:
                self._enqueue_status(StatusCode.CANCELLED, pending.seq, "cancelled")

    def has_pending(self) -> bool:
        with self.lock:
            for item in self.slots.values():
                if item is not None:
                    return True
            return any(seq is not None for seq in self.inflight.values())
