from __future__ import annotations

import asyncio
import concurrent.futures
import os
import sys
import threading
from collections import deque
from dataclasses import dataclass

from obs_captions.config import load_config
from obs_captions.ipc.framing import (
    Audio,
    Control,
    FrameDecoder,
    FrameError,
    MsgType,
    NeedMoreData,
    StatusCode,
    Hello,
    encode_caption_final,
    encode_caption_partial,
    encode_flush_done,
    encode_ready,
    encode_status,
)
from obs_captions.stt.base import STTBackend, Transcript
from obs_captions.stt.registry import backend_cpu_bound, create_backend

_offload_thread_state = threading.local()


CONTROL_START = 1
CONTROL_STOP = 2
CONTROL_FLUSH = 3
CONTROL_RECONFIGURE = 4
CONTROL_COMMANDS = (CONTROL_START, CONTROL_STOP, CONTROL_FLUSH, CONTROL_RECONFIGURE)


@dataclass
class _QueuedAudio:
    epoch: int
    timestamp_ns: int
    pcm16: bytes


@dataclass
class _QueuedControl:
    command: int
    seq: int
    arg: str


class _AsyncWriter:
    async def write(self, data: bytes) -> None:  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover
        pass


class _PipeWriter(_AsyncWriter):
    def __init__(self, transport: asyncio.Transport, stdout_file: object) -> None:
        self._transport = transport
        self._stdout_file = stdout_file

    async def write(self, data: bytes) -> None:
        self._transport.write(data)

    async def close(self) -> None:
        if hasattr(self._transport, "close"):
            self._transport.close()
        if hasattr(self._stdout_file, "close"):
            self._stdout_file.close()  # type: ignore[attr-defined]


class _WriteProtocol(asyncio.Protocol):
    def __init__(self) -> None:
        self.transport: asyncio.Transport | None = None
        self.ready = asyncio.Event()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport
        self.ready.set()


class SidecarRuntime:
    def __init__(
        self,
        *,
        config_path: str | None,
        stdin_reader: object,
        writer: _AsyncWriter | None,
        max_audio_queue: int,
        max_out_queue: int,
        offload_feed_audio: bool,
    ) -> None:
        self._config_path = config_path
        self._stdin_reader = stdin_reader
        self._writer = writer
        self._max_audio_queue = max_audio_queue
        self._max_out_queue = max_out_queue
        self._offload_feed_audio = offload_feed_audio

        self._backend: STTBackend | None = None
        self._backend_token = 0
        self._session_token = 0
        self._session_epoch = 0
        self._session_path: str | None = config_path
        self._session_active = False
        self._session_has_context = False
        self._supports_partial = 1
        self._caption_seq = 0
        self._session_ready = asyncio.Event()
        self._session_ready.clear()

        self._hello_queue: deque[Hello] = deque()
        self._hello_lock = threading.Lock()
        self._pending_hello_epoch: int | None = None

        self._audio_queue: deque[_QueuedAudio] = deque()
        self._audio_lock = threading.Lock()
        self._audio_in_flight = 0
        self._dropped_inbound = 0

        self._out_queue: deque[bytes] = deque()
        self._out_lock = threading.Lock()
        self._dropped_outbound = 0

        self._control_slots: dict[int, _QueuedControl | None] = {
            CONTROL_START: None,
            CONTROL_STOP: None,
            CONTROL_FLUSH: None,
            CONTROL_RECONFIGURE: None,
        }
        self._control_inflight: dict[int, int | None] = {
            CONTROL_START: None,
            CONTROL_STOP: None,
            CONTROL_FLUSH: None,
            CONTROL_RECONFIGURE: None,
        }
        self._control_last_seq: dict[int, int] = {
            CONTROL_START: -1,
            CONTROL_STOP: -1,
            CONTROL_FLUSH: -1,
            CONTROL_RECONFIGURE: -1,
        }
        self._control_lock = threading.Lock()

        self._reader_done = threading.Event()
        self._reader_exception: Exception | None = None
        self._stopped = False
        self._reader_thread: threading.Thread | None = None
        self._reader_thread_ident: int | None = None

        self._supports_partial_lock = threading.Lock()
        self._caption_seq_lock = threading.Lock()
        self._offload_audio_executor: concurrent.futures.Executor | None = None

        self._run_tasks: list[asyncio.Task[None]] = []

        self._orig_stdout = sys.stdout

    @property
    def audio_queue_depth(self) -> int:
        with self._audio_lock:
            return len(self._audio_queue)

    @property
    def dropped_inbound(self) -> int:
        return self._dropped_inbound

    @property
    def dropped_outbound(self) -> int:
        return self._dropped_outbound

    @property
    def reader_thread_ident(self) -> int | None:
        return self._reader_thread_ident

    async def run(self) -> None:
        self._guard_stdout_to_stderr()

        if self._writer is None:
            self._writer = await self._create_default_writer()

        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        self._run_tasks = [
            asyncio.create_task(self._control_and_hello_loop()),
            asyncio.create_task(self._audio_loop()),
            asyncio.create_task(self._drain_outbound()),
        ]

        try:
            await self._await_end_of_work()
        finally:
            self._stopped = True
            await self._shutdown_tasks()
            await self._close_writer()
            sys.stdout = self._orig_stdout
            if self._reader_thread is not None:
                self._reader_thread.join(timeout=1.0)

    def _guard_stdout_to_stderr(self) -> None:
        sys.stdout = sys.stderr

    async def _create_default_writer(self) -> _AsyncWriter:
        loop = asyncio.get_running_loop()

        protocol = _WriteProtocol()
        stdout_file = os.fdopen(os.dup(self._orig_stdout.fileno()), "wb", buffering=0)
        transport, _ = await loop.connect_write_pipe(lambda: protocol, stdout_file)
        await protocol.ready.wait()
        assert protocol.transport is not None
        return _PipeWriter(protocol.transport, stdout_file)

    async def _close_writer(self) -> None:
        if self._writer is not None:
            await self._writer.close()

    def _reader_loop(self) -> None:
        self._reader_thread_ident = threading.get_ident()
        decoder = FrameDecoder()
        while True:
            chunk = self._stdin_reader.read(65536)  # type: ignore[attr-defined]
            if not chunk:
                self._reader_done.set()
                return

            try:
                for msg_type, payload in decoder.feed(chunk):
                    if msg_type == MsgType.HELLO:
                        self._enqueue_hello(payload)
                    elif msg_type == MsgType.AUDIO:
                        self._enqueue_audio(payload)
                    elif msg_type == MsgType.CONTROL:
                        self._enqueue_control(payload)
            except NeedMoreData:
                continue
            except FrameError as exc:
                self._reader_exception = exc
                self._enqueue_status(StatusCode.RUNTIME_ERROR, 0, f"framing: {exc}")
                self._reader_done.set()
                return

    def _enqueue_hello(self, hello: Hello) -> None:
        with self._hello_lock:
            self._hello_queue.append(hello)
            self._pending_hello_epoch = hello.epoch
            self._session_has_context = True
            self._session_ready.clear()

    def _enqueue_audio(self, audio: Audio) -> None:
        if self._session_active:
            epoch = self._session_epoch
        elif self._pending_hello_epoch is not None:
            epoch = self._pending_hello_epoch
        else:
            self._dropped_inbound += 1
            return

        pcm16 = audio.pcm
        item = _QueuedAudio(
            epoch=epoch,
            timestamp_ns=audio.timestamp_ns,
            pcm16=pcm16,
        )

        with self._audio_lock:
            if len(self._audio_queue) >= self._max_audio_queue:
                self._audio_queue.popleft()
                self._dropped_inbound += 1
            self._audio_queue.append(item)

    def _enqueue_control(self, control: Control) -> None:
        if control.command not in CONTROL_COMMANDS:
            self._enqueue_status(StatusCode.RUNTIME_ERROR, control.seq, "unknown control command")
            return

        with self._control_lock:
            if not self._session_has_context:
                self._enqueue_status(StatusCode.NO_SESSION, control.seq, "no session")
                return

            if self._control_inflight[control.command] is not None:
                if self._control_inflight[control.command] == control.seq:
                    self._enqueue_status(StatusCode.OK, control.seq, "ack")
                    return
                self._enqueue_status(StatusCode.CANCELLED, control.seq, "superseded")
                return

            last_seq = self._control_last_seq[control.command]
            if control.seq == last_seq:
                self._enqueue_status(StatusCode.OK, control.seq, "ack")
                return
            if control.seq < last_seq:
                self._enqueue_status(StatusCode.CANCELLED, control.seq, "superseded")
                return

            prior = self._control_slots[control.command]
            if prior is not None:
                self._enqueue_status(StatusCode.CANCELLED, prior.seq, "superseded")
            self._control_slots[control.command] = _QueuedControl(
                command=control.command,
                seq=control.seq,
                arg=control.arg,
            )

    async def _control_and_hello_loop(self) -> None:
        while True:
            if self._stopped:
                return

            await self._drain_hello_once()
            await self._drain_control_once()

            if self._should_stop():
                self._stopped = True
                return

            await asyncio.sleep(0.001)

    async def _drain_hello_once(self) -> None:
        while True:
            with self._hello_lock:
                if not self._hello_queue:
                    return
                hello = self._hello_queue.popleft()

            await self._start_session(hello.config_path, hello.epoch, hello.proto_version)

    async def _start_session(
        self,
        config_path: str,
        epoch: int,
        accepted_version: int,
        ack_seq: int = 0,
    ) -> bool:
        self._session_active = False
        self._session_ready.clear()
        self._session_has_context = True
        self._pending_hello_epoch = epoch

        old_backend = self._backend
        self._backend = None

        self._prune_audio_for_epoch(epoch)

        if old_backend is not None:
            try:
                await old_backend.stop_stream()
            except Exception:
                pass

        try:
            cfg = load_config(config_path)
        except Exception as exc:
            self._session_path = config_path
            self._session_has_context = False
            self._enqueue_status(StatusCode.CONFIG_ERROR, ack_seq, f"config load failed: {exc}")
            self._clear_pending_controls()
            return False

        token = self._session_token + 1
        self._session_token = token

        try:
            backend = create_backend(
                cfg,
                on_partial=lambda tr, _token=token: self._on_transcript(_token, tr),
                on_final=lambda tr, _token=token: self._on_transcript(_token, tr),
            )
        except Exception as exc:
            self._session_has_context = False
            self._enqueue_status(StatusCode.ENGINE_INIT_FAIL, ack_seq, str(exc))
            self._clear_pending_controls()
            return False

        with self._supports_partial_lock:
            self._supports_partial = int(bool(getattr(backend, "supports_partial", 1)))

        try:
            await backend.start_stream()
        except Exception as exc:
            self._session_has_context = False
            self._enqueue_status(StatusCode.ENGINE_INIT_FAIL, ack_seq, str(exc))
            self._clear_pending_controls()
            return False

        with self._supports_partial_lock:
            self._supports_partial = int(bool(getattr(backend, "supports_partial", self._supports_partial)))

        with self._caption_seq_lock:
            self._caption_seq = 0

        self._backend = backend
        self._offload_feed_audio = backend_cpu_bound(cfg)
        self._backend_token = token
        self._session_epoch = epoch
        self._session_path = config_path
        self._session_active = True
        self._session_ready.set()
        self._pending_hello_epoch = None

        self._enqueue_out(
            encode_ready(
                accepted_version=accepted_version,
                epoch=epoch,
                engine_name=cfg.engine,
                language=cfg.language,
                supports_partial=self._supports_partial,
            )
        )
        self._enqueue_status(StatusCode.OK, 0, "ready")
        return True

    async def _drain_control_once(self) -> None:
        for command in CONTROL_COMMANDS:
            control = None
            with self._control_lock:
                if self._control_inflight[command] is not None:
                    continue
                control = self._control_slots[command]
                if control is None:
                    continue
                self._control_slots[command] = None
                self._control_inflight[command] = control.seq

            if control is None:
                continue

            if (
                control.command in (CONTROL_FLUSH, CONTROL_RECONFIGURE)
                and self._backend is None
            ):
                if self._session_has_context and not self._session_active:
                    with self._control_lock:
                        self._control_slots[command] = control
                        self._control_inflight[command] = None
                    await asyncio.sleep(0.001)
                    continue

                with self._control_lock:
                    self._control_inflight[command] = None

                self._enqueue_status(StatusCode.NO_SESSION, control.seq, "no session")
                continue

            try:
                await self._run_control(control)
            except Exception as exc:  # noqa: BLE001
                self._enqueue_status(StatusCode.RUNTIME_ERROR, control.seq, str(exc))
            finally:
                with self._control_lock:
                    self._control_last_seq[command] = control.seq
                    self._control_inflight[command] = None

    async def _run_control(self, control: _QueuedControl) -> None:
        if control.command == CONTROL_START:
            await self._handle_start(control)
        elif control.command == CONTROL_STOP:
            await self._handle_stop(control)
        elif control.command == CONTROL_FLUSH:
            await self._handle_flush(control)
        elif control.command == CONTROL_RECONFIGURE:
            await self._handle_reconfigure(control)

    async def _handle_start(self, control: _QueuedControl) -> None:
        if self._backend is None:
            self._enqueue_status(StatusCode.NO_SESSION, control.seq, "no session")
            return
        self._enqueue_status(StatusCode.OK, control.seq, "ack")

    async def _handle_stop(self, control: _QueuedControl) -> None:
        if self._backend is not None:
            try:
                await self._backend.stop_stream()
            except Exception:
                pass
            self._backend = None

        self._session_active = False
        self._session_ready.clear()
        self._session_has_context = False
        self._pending_hello_epoch = None

        self._clear_pending_controls()
        self._enqueue_status(StatusCode.OK, control.seq, "ack")

    async def _handle_flush(self, control: _QueuedControl) -> None:
        if self._backend is None:
            self._enqueue_status(StatusCode.NO_SESSION, control.seq, "no session")
            return

        await self._backend.flush()
        self._enqueue_flush_done(control.seq)

    async def _handle_reconfigure(self, control: _QueuedControl) -> None:
        if not control.arg:
            self._enqueue_status(StatusCode.CONFIG_ERROR, control.seq, "missing config path")
            return

        started = await self._start_session(
            control.arg, self._session_epoch + 1, 1, control.seq
        )
        if started:
            self._enqueue_status(StatusCode.OK, control.seq, "ack")

    async def _audio_loop(self) -> None:
        while True:
            if self._stopped:
                return

            item = self._pop_audio()
            if item is None:
                await asyncio.sleep(0.001)
                continue

            with self._audio_lock:
                self._audio_in_flight += 1
            try:
                if item.epoch != self._session_epoch:
                    continue

                backend = self._backend
                if backend is None:
                    continue

                if self._offload_feed_audio:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        self._get_offload_executor(),
                        self._run_coro_sync,
                        backend,
                        item.pcm16,
                    )
                else:
                    await backend.feed_audio(item.pcm16)
            except Exception as exc:  # noqa: BLE001
                self._enqueue_status(StatusCode.RUNTIME_ERROR, 0, str(exc))
                continue
            finally:
                with self._audio_lock:
                    self._audio_in_flight -= 1

    def _get_offload_executor(self) -> concurrent.futures.Executor:
        if self._offload_audio_executor is None:
            self._offload_audio_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        return self._offload_audio_executor

    @staticmethod
    def _run_coro_sync(backend: STTBackend, pcm16: bytes) -> None:
        loop = getattr(_offload_thread_state, "loop", None)
        if loop is None:
            loop = asyncio.new_event_loop()
            _offload_thread_state.loop = loop
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            _offload_thread_state.loop = loop
        loop.run_until_complete(backend.feed_audio(pcm16))

    def _pop_audio(self) -> _QueuedAudio | None:
        with self._audio_lock:
            if not self._audio_queue:
                return None
            return self._audio_queue.popleft()

    def _clear_pending_controls(self) -> None:
        for command in CONTROL_COMMANDS:
            with self._control_lock:
                pending = self._control_slots.get(command)
                self._control_slots[command] = None
                self._control_inflight[command] = None
            if pending is not None:
                self._enqueue_status(StatusCode.CANCELLED, pending.seq, "cancelled")

    def _prune_audio_for_epoch(self, epoch: int) -> None:
        with self._audio_lock:
            if not self._audio_queue:
                return
            self._audio_queue = deque(
                item for item in self._audio_queue if item.epoch == epoch
            )

    def _on_transcript(self, token: int, transcript: Transcript) -> None:
        if token != self._backend_token:
            return
        if not self._session_active:
            return

        with self._supports_partial_lock:
            supports_partial = self._supports_partial

        if not transcript.is_final and supports_partial == 0:
            return

        timestamp_ms = transcript.start_ms or 0
        timestamp_ns = timestamp_ms * 1_000_000

        with self._caption_seq_lock:
            self._caption_seq += 1
            seq = self._caption_seq

        self._enqueue_caption(seq, transcript.text, timestamp_ns, transcript.is_final)

    def _enqueue_caption(self, seq: int, text: str, timestamp_ns: int, is_final: bool) -> None:
        if is_final:
            frame = encode_caption_final(
                epoch=self._session_epoch,
                timestamp_ns=timestamp_ns,
                seq=seq,
                text=text,
            )
        else:
            frame = encode_caption_partial(
                epoch=self._session_epoch,
                timestamp_ns=timestamp_ns,
                seq=seq,
                text=text,
            )
        self._enqueue_out(frame)

    def _enqueue_status(self, code: StatusCode, ack_seq: int, message: str) -> None:
        self._enqueue_out(encode_status(code, ack_seq, message))

    def _enqueue_flush_done(self, seq: int) -> None:
        self._enqueue_out(encode_flush_done(seq))

    def _enqueue_out(self, payload: bytes) -> None:
        with self._out_lock:
            if len(self._out_queue) >= self._max_out_queue:
                self._out_queue.popleft()
                self._dropped_outbound += 1
            self._out_queue.append(payload)

    async def _drain_outbound(self) -> None:
        while True:
            if self._stopped:
                with self._out_lock:
                    if not self._out_queue:
                        return
                # fallthrough: flush remaining bytes before exit
            payload = None
            with self._out_lock:
                if self._out_queue:
                    payload = self._out_queue.popleft()
            if payload is None:
                await asyncio.sleep(0.001)
                continue
            await self._writer.write(payload)

    def _should_stop(self) -> bool:
        if not self._reader_done.is_set():
            return False
        if self._reader_exception is not None:
            return True

        with self._audio_lock:
            if self._audio_queue or self._audio_in_flight != 0:
                return False

        with self._hello_lock:
            if self._hello_queue:
                return False

        with self._control_lock:
            for item in self._control_slots.values():
                if item is not None:
                    return False
            if any(seq is not None for seq in self._control_inflight.values()):
                return False

        with self._out_lock:
            if self._out_queue:
                return False

        return True

    async def _await_end_of_work(self) -> None:
        while True:
            if self._should_stop():
                return
            await asyncio.sleep(0.001)

    async def _shutdown_tasks(self) -> None:
        for task in self._run_tasks:
            task.cancel()
        if self._run_tasks:
            await asyncio.gather(*self._run_tasks, return_exceptions=True)
        if self._offload_audio_executor is not None:
            self._offload_audio_executor.shutdown(wait=True)


async def run_sidecar(
    *,
    config_path: str | None = None,
    stdin_reader: object | None = None,
    writer: _AsyncWriter | None = None,
    max_audio_queue: int = 128,
    max_out_queue: int = 256,
    offload_feed_audio: bool = False,
    return_runtime: bool = False,
) -> SidecarRuntime | None:
    if stdin_reader is None:
        stdin_reader = sys.stdin.buffer

    runtime = SidecarRuntime(
        config_path=config_path,
        stdin_reader=stdin_reader,
        writer=writer,
        max_audio_queue=max_audio_queue,
        max_out_queue=max_out_queue,
        offload_feed_audio=offload_feed_audio,
    )
    await runtime.run()
    if return_runtime:
        return runtime
    return None


def run_sidecar_cli(config_path: str | None = None) -> None:
    asyncio.run(run_sidecar(config_path=config_path))
