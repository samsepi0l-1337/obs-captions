from __future__ import annotations

import asyncio
import io
import logging
import sys
import threading
from collections import Counter

import pytest

from obs_captions.ipc.framing import (
    MsgType,
    StatusCode,
    FrameDecoder,
    encode_audio,
    encode_control,
    encode_hello,
)
from obs_captions.ipc.sidecar import (
    CONTROL_FLUSH,
    CONTROL_RECONFIGURE,
    CONTROL_STOP,
    CONTROL_START,
    SidecarRuntime,
    run_sidecar,
)
from obs_captions.stt.base import STTBackend, Transcript


class FakeTransportWriter:
    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.thread_ids: set[int] = set()

    async def write(self, payload: bytes) -> None:
        self.thread_ids.add(threading.get_ident())
        self.frames.append(payload)

    async def close(self) -> None:
        pass


def _decode_frames(raw_frames: list[bytes]) -> list[tuple[MsgType, object]]:
    decoder = FrameDecoder()
    out: list[tuple[MsgType, object]] = []
    for frame in raw_frames:
        out.extend(decoder.feed(frame))
    return out


def _build_input(*parts: bytes) -> io.BytesIO:
    return io.BytesIO(b"".join(parts))


class RecordingBackend(STTBackend):
    def __init__(
        self,
        *,
        supports_partial: int,
        on_partial,
        on_final,
        start_counter: list[int] | None = None,
        flush_counter: list[int] | None = None,
        stop_counter: list[int] | None = None,
        on_feed: callable | None = None,
    ) -> None:
        super().__init__(language="ko", sample_rate=16_000, on_partial=on_partial, on_final=on_final)
        self.supports_partial = supports_partial
        self.start_counter = start_counter
        self.flush_counter = flush_counter
        self.stop_counter = stop_counter
        self.feed_calls = 0
        self.on_feed = on_feed

    async def start_stream(self) -> None:
        if self.start_counter is not None:
            self.start_counter.append(1)

    async def feed_audio(self, pcm16: bytes) -> None:
        self.feed_calls += 1
        if self.on_feed is not None:
            await self.on_feed(pcm16)
            return
        if self.supports_partial:
            self.on_partial(Transcript("partial", is_final=False, start_ms=10, end_ms=10, lang="ko"))
        self.on_final(Transcript("final", is_final=True, start_ms=20, end_ms=20, lang="ko"))

    async def flush(self) -> None:
        if self.flush_counter is not None:
            self.flush_counter.append(1)

    async def stop_stream(self) -> None:
        if self.stop_counter is not None:
            self.stop_counter.append(1)


class PollutingBackend(RecordingBackend):
    async def start_stream(self) -> None:
        print("probe")
        logging.getLogger("probe").warning("warn")
        await super().start_stream()


@pytest.mark.asyncio
async def test_ipc_sidecar_emits_partial_and_final_frames(monkeypatch, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("")
    start_counter: list[int] = []

    backend_ref: dict[str, RecordingBackend] = {}

    def fake_create_backend(config, *, on_partial, on_final):
        backend = RecordingBackend(
            supports_partial=1,
            on_partial=on_partial,
            on_final=on_final,
            start_counter=start_counter,
        )
        backend_ref["backend"] = backend
        return backend

    import obs_captions.ipc.sidecar as sidecar

    monkeypatch.setattr(sidecar, "create_backend", fake_create_backend)

    writer = FakeTransportWriter()
    await run_sidecar(
        stdin_reader=_build_input(
            encode_hello(1, 1, 16_000, 1, 1, str(cfg)),
            encode_audio(100, 2, [0, 0]),
        ),
        writer=writer,
    )

    messages = _decode_frames(writer.frames)
    msg_types = [msg for msg, _ in messages]
    assert MsgType.READY in msg_types
    assert MsgType.CAPTION_PARTIAL in msg_types
    assert MsgType.CAPTION_FINAL in msg_types

    backend = backend_ref["backend"]
    assert backend.start_counter == [1]
    assert backend.feed_calls == 1


@pytest.mark.asyncio
async def test_ipc_sidecar_stderr_isolated_from_protocol_output(monkeypatch, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("")

    states = {"stdout_is_stderr": False}

    class ProbedBackend(PollutingBackend):
        async def start_stream(self) -> None:
            states["stdout_is_stderr"] = sys.stdout is sys.stderr
            await super().start_stream()

    def fake_create_backend(config, *, on_partial, on_final):
        return ProbedBackend(supports_partial=1, on_partial=on_partial, on_final=on_final)

    import obs_captions.ipc.sidecar as sidecar

    monkeypatch.setattr(sidecar, "create_backend", fake_create_backend)

    writer = FakeTransportWriter()
    await run_sidecar(
        stdin_reader=_build_input(
            encode_hello(1, 1, 16_000, 1, 1, str(cfg)),
            encode_audio(100, 1, [1]),
        ),
        writer=writer,
    )

    assert states["stdout_is_stderr"] is True
    messages = _decode_frames(writer.frames)
    assert any(msg in (MsgType.CAPTION_PARTIAL, MsgType.CAPTION_FINAL) for msg, _ in messages)


@pytest.mark.asyncio
async def test_ipc_sidecar_flush_control_emits_flush_done(monkeypatch, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("")

    flush_counter: list[int] = []

    def fake_create_backend(config, *, on_partial, on_final):
        return RecordingBackend(
            supports_partial=1,
            on_partial=on_partial,
            on_final=on_final,
            flush_counter=flush_counter,
        )

    import obs_captions.ipc.sidecar as sidecar

    monkeypatch.setattr(sidecar, "create_backend", fake_create_backend)

    writer = FakeTransportWriter()
    await run_sidecar(
        stdin_reader=_build_input(
            encode_hello(1, 1, 16_000, 1, 1, str(cfg)),
            encode_control(CONTROL_FLUSH, 42, ""),
        ),
        writer=writer,
    )

    messages = _decode_frames(writer.frames)
    assert (MsgType.FLUSH_DONE, 42) in [
        (msg_type, payload.seq) for msg_type, payload in messages if msg_type == MsgType.FLUSH_DONE
    ]
    assert flush_counter == [1]


@pytest.mark.asyncio
async def test_ipc_sidecar_reconfigure_same_seq_is_idempotent(monkeypatch, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("")
    cfg2 = tmp_path / "config2.toml"
    cfg2.write_text("")

    start_counter: list[int] = []

    def fake_create_backend(config, *, on_partial, on_final):
        return RecordingBackend(
            supports_partial=1,
            on_partial=on_partial,
            on_final=on_final,
            start_counter=start_counter,
        )

    import obs_captions.ipc.sidecar as sidecar

    monkeypatch.setattr(sidecar, "create_backend", fake_create_backend)

    writer = FakeTransportWriter()
    await run_sidecar(
        stdin_reader=_build_input(
            encode_hello(1, 1, 16_000, 1, 1, str(cfg)),
            encode_control(CONTROL_RECONFIGURE, 7, str(cfg2)),
            encode_control(CONTROL_RECONFIGURE, 7, str(cfg2)),
        ),
        writer=writer,
    )

    messages = _decode_frames(writer.frames)
    status = [
        (payload.ack_seq, payload.code)
        for msg_type, payload in messages
        if msg_type == MsgType.STATUS
    ]
    assert [seq for seq, code in status if seq == 7] == [7, 7]

    # hello start + first reconfigure start
    assert len(start_counter) == 2


@pytest.mark.asyncio
async def test_ipc_sidecar_batch_backend_no_partial_frames(monkeypatch, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("")

    def fake_create_backend(config, *, on_partial, on_final):
        return RecordingBackend(
            supports_partial=0,
            on_partial=on_partial,
            on_final=on_final,
        )

    import obs_captions.ipc.sidecar as sidecar

    monkeypatch.setattr(sidecar, "create_backend", fake_create_backend)

    writer = FakeTransportWriter()
    await run_sidecar(
        stdin_reader=_build_input(
            encode_hello(1, 1, 16_000, 1, 1, str(cfg)),
            encode_audio(100, 1, [10]),
        ),
        writer=writer,
    )

    messages = _decode_frames(writer.frames)
    msg_types = [msg for msg, _ in messages]
    assert MsgType.CAPTION_PARTIAL not in msg_types
    assert MsgType.CAPTION_FINAL in msg_types


@pytest.mark.asyncio
async def test_ipc_sidecar_stalled_feed_backpressure_stays_within_bounded_audio_queue(monkeypatch, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("")

    gate = threading.Event()

    async def wait_gate(_: bytes) -> None:
        while not gate.is_set():
            await asyncio.sleep(0.01)

    def fake_create_backend(config, *, on_partial, on_final):
        return RecordingBackend(
            supports_partial=1,
            on_partial=on_partial,
            on_final=on_final,
            on_feed=wait_gate,
        )

    import obs_captions.ipc.sidecar as sidecar

    monkeypatch.setattr(sidecar, "create_backend", fake_create_backend)

    audio_frames = [encode_audio(100 + i, 1, [i]) for i in range(40)]
    input_bytes = encode_hello(1, 1, 16_000, 1, 1, str(cfg)) + b"".join(audio_frames)

    writer = FakeTransportWriter()
    runtime = SidecarRuntime(
        config_path=str(cfg),
        stdin_reader=_build_input(input_bytes),
        writer=writer,
        max_audio_queue=8,
        max_out_queue=32,
        offload_feed_audio=False,
    )

    task = asyncio.create_task(runtime.run())
    await asyncio.sleep(0.2)
    assert runtime.audio_queue_depth <= 8
    gate.set()
    await task

    assert runtime.dropped_inbound > 0


@pytest.mark.asyncio
async def test_ipc_sidecar_controls_complete_once_and_written_via_single_out_queue(monkeypatch, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("")
    cfg2 = tmp_path / "config2.toml"
    cfg2.write_text("")

    def fake_create_backend(config, *, on_partial, on_final):
        return RecordingBackend(supports_partial=1, on_partial=on_partial, on_final=on_final)

    import obs_captions.ipc.sidecar as sidecar

    monkeypatch.setattr(sidecar, "create_backend", fake_create_backend)

    writer = FakeTransportWriter()
    runtime = SidecarRuntime(
        config_path=str(cfg),
        stdin_reader=_build_input(
            encode_hello(1, 1, 16_000, 1, 1, str(cfg)),
            encode_control(CONTROL_START, 1, ""),
            encode_control(CONTROL_START, 2, ""),
            encode_control(CONTROL_FLUSH, 3, ""),
            encode_control(CONTROL_RECONFIGURE, 4, str(cfg2)),
        ),
        writer=writer,
        max_audio_queue=16,
        max_out_queue=32,
        offload_feed_audio=False,
    )

    await runtime.run()
    messages = _decode_frames(writer.frames)

    status = Counter(
        (payload.ack_seq, payload.code)
        for msg_type, payload in messages
        if msg_type == MsgType.STATUS
    )
    assert status[(1, StatusCode.CANCELLED)] == 1
    assert status[(2, StatusCode.OK)] == 1
    assert status[(4, StatusCode.OK)] == 1
    assert status[(4, StatusCode.CANCELLED)] == 0

    flush_done = [payload.seq for msg_type, payload in messages if msg_type == MsgType.FLUSH_DONE]
    assert flush_done == [3]

    assert len(writer.thread_ids) == 1
    assert runtime.reader_thread_ident is not None
    assert runtime.reader_thread_ident not in writer.thread_ids


@pytest.mark.asyncio
async def test_ipc_sidecar_reconfigure_missing_config_path_reports_error_status(monkeypatch, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("")
    missing = tmp_path / "missing.toml"

    writer = FakeTransportWriter()
    await run_sidecar(
        stdin_reader=_build_input(
            encode_hello(1, 1, 16_000, 1, 1, str(cfg)),
            encode_control(CONTROL_RECONFIGURE, 7, str(missing)),
        ),
        writer=writer,
    )

    messages = _decode_frames(writer.frames)
    status = [
        (payload.ack_seq, payload.code)
        for msg_type, payload in messages
        if msg_type == MsgType.STATUS
    ]
    assert (7, StatusCode.OK) not in status
    assert any(seq == 7 and code != StatusCode.OK for seq, code in status)


@pytest.mark.asyncio
async def test_ipc_sidecar_flush_exception_reports_runtime_error_and_loop_continues(monkeypatch, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("")
    cfg2 = tmp_path / "config2.toml"
    cfg2.write_text("")

    def fake_create_backend(config, *, on_partial, on_final):
        class Backend(RecordingBackend):
            async def flush(self) -> None:
                self.flush_counter.append(1)
                raise RuntimeError("flush failed")

        return Backend(supports_partial=1, on_partial=on_partial, on_final=on_final, flush_counter=[])

    import obs_captions.ipc.sidecar as sidecar

    monkeypatch.setattr(sidecar, "create_backend", fake_create_backend)

    writer = FakeTransportWriter()
    await run_sidecar(
        stdin_reader=_build_input(
            encode_hello(1, 1, 16_000, 1, 1, str(cfg)),
            encode_control(CONTROL_FLUSH, 42, ""),
            encode_control(CONTROL_RECONFIGURE, 43, str(cfg2)),
        ),
        writer=writer,
    )

    messages = _decode_frames(writer.frames)
    status = Counter(
        (payload.ack_seq, payload.code)
        for msg_type, payload in messages
        if msg_type == MsgType.STATUS
    )
    assert status[(42, StatusCode.RUNTIME_ERROR)] == 1
    assert status[(43, StatusCode.OK)] == 1

    assert not any(msg_type == MsgType.FLUSH_DONE for msg_type, _ in messages)


@pytest.mark.asyncio
async def test_ipc_sidecar_offload_reuses_event_loop_for_multiple_audio_chunks(monkeypatch, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("")
    loop_ids: list[int] = []

    async def probe_feed(_: bytes) -> None:
        loop_ids.append(id(asyncio.get_running_loop()))
        await asyncio.sleep(0)

    def fake_create_backend(config, *, on_partial, on_final):
        return RecordingBackend(
            supports_partial=0,
            on_partial=on_partial,
            on_final=on_final,
            on_feed=probe_feed,
        )

    import obs_captions.ipc.sidecar as sidecar

    monkeypatch.setattr(sidecar, "create_backend", fake_create_backend)

    original_new_event_loop = sidecar.asyncio.new_event_loop
    created_loops: list[object] = []

    def spy_new_event_loop() -> object:
        loop = original_new_event_loop()
        created_loops.append(loop)
        return loop

    monkeypatch.setattr(sidecar.asyncio, "new_event_loop", spy_new_event_loop)

    writer = FakeTransportWriter()
    await run_sidecar(
        stdin_reader=_build_input(
            encode_hello(1, 1, 16_000, 1, 1, str(cfg)),
            encode_audio(100, 1, [1]),
            encode_audio(101, 1, [2]),
            encode_audio(102, 1, [3]),
        ),
        writer=writer,
        offload_feed_audio=False,
    )

    assert len(loop_ids) >= 2
    assert len(set(loop_ids)) == 1
    assert len(created_loops) == 1


@pytest.mark.asyncio
async def test_ipc_sidecar_control_stop_dispatches_and_resets_runtime_state(monkeypatch, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("")
    start_counter: list[int] = []
    stop_counter: list[int] = []
    stop_dispatch = {"count": 0}

    def fake_create_backend(config, *, on_partial, on_final):
        return RecordingBackend(
            supports_partial=1,
            on_partial=on_partial,
            on_final=on_final,
            start_counter=start_counter,
            stop_counter=stop_counter,
        )

    import obs_captions.ipc.sidecar as sidecar

    monkeypatch.setattr(sidecar, "create_backend", fake_create_backend)

    original_handle_stop = sidecar.SidecarRuntime._handle_stop

    async def handle_stop_probe(self: SidecarRuntime, control) -> None:
        stop_dispatch["count"] += 1
        await original_handle_stop(self, control)

    monkeypatch.setattr(sidecar.SidecarRuntime, "_handle_stop", handle_stop_probe)

    writer = FakeTransportWriter()
    runtime = await run_sidecar(
        stdin_reader=_build_input(
            encode_hello(1, 1, 16_000, 1, 1, str(cfg)),
            encode_audio(100, 1, [1]),
            encode_control(CONTROL_STOP, 7, ""),
        ),
        writer=writer,
        return_runtime=True,
    )

    assert runtime is not None
    messages = _decode_frames(writer.frames)
    status = Counter(
        (payload.ack_seq, payload.code)
        for msg_type, payload in messages
        if msg_type == MsgType.STATUS
    )
    assert status[(0, StatusCode.OK)] == 1
    assert status[(7, StatusCode.OK)] == 1

    assert stop_dispatch["count"] == 1
    assert runtime._backend is None
    assert runtime._session_active is False
    assert runtime._session_has_context is False
    assert runtime._pending_hello_epoch is None
    assert runtime._session_ready.is_set() is False
    assert runtime._control_slots[CONTROL_STOP] is None
    assert start_counter == [1]
    assert stop_counter == [1]
