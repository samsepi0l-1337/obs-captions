from __future__ import annotations

import asyncio
import sys

import numpy as np
import pytest

from obs_captions.audio.capture import MicCapture
from obs_captions.audio.devices import InputDevice, list_loopback_devices
from obs_captions.audio.loopback import (
    LoopbackDevice,
    LoopbackStream,
    make_loopback_stream_factory,
    resolve_loopback_device,
)
from obs_captions.cli import make_capture
from obs_captions.config import AppConfig


class FakePyAudioStream:
    """Mirrors a PyAudioWPatch stream (start_stream/stop_stream/close)."""

    def __init__(self, *, fail_start=False, fail_close=False, **kwargs):
        self.open_kwargs = kwargs
        self.stream_callback = kwargs.get("stream_callback")
        self.started = False
        self.stopped = False
        self.closed = False
        self.close_count = 0
        self._fail_start = fail_start
        self._fail_close = fail_close

    def start_stream(self):
        if self._fail_start:
            raise RuntimeError("start_stream boom")
        self.started = True

    def stop_stream(self):
        self.stopped = True

    def close(self):
        self.close_count += 1  # count the attempt even when it raises
        if self._fail_close:
            raise RuntimeError("close boom")
        self.closed = True


class FakePyAudio:
    """Mirrors a PyAudioWPatch ``PyAudio`` instance."""

    def __init__(
        self,
        *,
        default_info=None,
        infos=None,
        fail_open=False,
        fail_start=False,
        fail_close=False,
        fail_terminate=False,
    ):
        self._default_info = default_info
        self._infos = list(infos or [])
        self.streams: list[FakePyAudioStream] = []
        self.terminated = False
        self.terminate_count = 0
        self._fail_open = fail_open
        self._fail_start = fail_start
        self._fail_close = fail_close
        self._fail_terminate = fail_terminate

    def open(self, **kwargs):
        if self._fail_open:
            raise RuntimeError("open boom")
        stream = FakePyAudioStream(
            fail_start=self._fail_start, fail_close=self._fail_close, **kwargs
        )
        self.streams.append(stream)
        return stream

    def get_default_wasapi_loopback(self):
        return self._default_info

    def get_loopback_device_info_generator(self):
        yield from self._infos

    def terminate(self):
        self.terminate_count += 1  # count the attempt even when it raises
        if self._fail_terminate:
            raise RuntimeError("terminate boom")
        self.terminated = True


class FakePyAudioModule:
    """Stand-in for the lazily-imported ``pyaudiowpatch`` module."""

    paFloat32 = "paFloat32-sentinel"
    paContinue = "paContinue-sentinel"

    def __init__(
        self,
        *,
        default_info=None,
        infos=None,
        fail_open_times=0,
        fail_start=False,
        fail_close=False,
        fail_terminate=False,
    ):
        self._default_info = default_info
        self._infos = list(infos or [])
        self.instances: list[FakePyAudio] = []
        self._fail_open_times = fail_open_times
        self._fail_start = fail_start
        self._fail_close = fail_close
        self._fail_terminate = fail_terminate

    def PyAudio(self):
        fail_open = self._fail_open_times > 0
        if fail_open:
            self._fail_open_times -= 1
        inst = FakePyAudio(
            default_info=self._default_info,
            infos=self._infos,
            fail_open=fail_open,
            fail_start=self._fail_start,
            fail_close=self._fail_close,
            fail_terminate=self._fail_terminate,
        )
        self.instances.append(inst)
        return inst


def _interleave_stereo(left: np.ndarray, right: np.ndarray) -> bytes:
    interleaved = np.empty(left.size + right.size, dtype=np.float32)
    interleaved[0::2] = left
    interleaved[1::2] = right
    return interleaved.tobytes()


def test_loopback_stream_opens_pafloat32_and_native_channels():
    module = FakePyAudioModule()
    stream = LoopbackStream(
        samplerate=48000,
        device_channels=2,
        blocksize=4800,
        device=7,
        callback=lambda *a: None,
        pyaudio_module=module,
    )

    stream.start()

    opened = module.instances[0].streams[0]
    assert opened.open_kwargs["format"] == FakePyAudioModule.paFloat32
    assert opened.open_kwargs["channels"] == 2
    assert opened.open_kwargs["rate"] == 48000
    assert opened.open_kwargs["input"] is True
    assert opened.open_kwargs["input_device_index"] == 7
    assert opened.open_kwargs["frames_per_buffer"] == 4800
    assert opened.started is True


@pytest.mark.asyncio
async def test_loopback_adapter_downmixes_and_resamples_into_miccapture():
    module = FakePyAudioModule()
    factory = make_loopback_stream_factory(device_channels=2, pyaudio_module=module)
    capture = MicCapture(
        device=7,
        samplerate=48000,
        channels=1,
        blocksize=4800,
        stream_factory=factory,
    )
    capture.start()

    # 480 stereo frames @48k: channel 0 = 0.5, channel 1 = -1.0.
    left = np.full(480, 0.5, dtype=np.float32)
    right = np.full(480, -1.0, dtype=np.float32)
    in_data = _interleave_stereo(left, right)

    pa_stream = module.instances[0].streams[0]
    result = pa_stream.stream_callback(in_data, 480, None, None)
    assert result == (None, FakePyAudioModule.paContinue)

    pcm = await asyncio.wait_for(capture.read(), timeout=1)
    await capture.stop()

    samples = np.frombuffer(pcm, dtype="<i2")
    # 480 frames @48k -> 160 samples @16k (resample); value 16383 == channel 0 (0.5)
    # downmix, NOT the channel-1 (-1.0) or an average (-0.25).
    assert len(samples) == 160
    assert set(samples.tolist()) == {16383}


def test_loopback_stream_lifecycle_maps_to_pyaudio_and_terminates():
    module = FakePyAudioModule()
    stream = LoopbackStream(
        samplerate=48000,
        device_channels=2,
        blocksize=4800,
        device=0,
        callback=lambda *a: None,
        pyaudio_module=module,
    )

    stream.start()
    pa = module.instances[0]
    opened = pa.streams[0]
    stream.stop()
    stream.close()

    assert opened.started is True
    assert opened.stopped is True
    assert opened.closed is True
    assert pa.terminated is True  # owns + releases the PyAudio instance (no leak)


def _make_stream(module, *, device=0):
    return LoopbackStream(
        samplerate=48000,
        device_channels=2,
        blocksize=4800,
        device=device,
        callback=lambda *a: None,
        pyaudio_module=module,
    )


def test_loopback_start_open_failure_terminates_and_retry_does_not_orphan():
    # Bug 1: a failed open() must not leave a live PyAudio instance behind that a
    # subsequent start() (reconnect) would overwrite -> orphaned native handle.
    module = FakePyAudioModule(fail_open_times=1)
    stream = _make_stream(module)

    with pytest.raises(RuntimeError, match="open boom"):
        stream.start()

    assert len(module.instances) == 1
    assert module.instances[0].terminate_count == 1  # terminated, not orphaned
    assert stream._pa is None
    assert stream._stream is None

    # Reconnect: a fresh instance opens cleanly; the first stays released (no orphan).
    stream.start()
    assert len(module.instances) == 2
    assert module.instances[0].terminate_count == 1  # first not leaked, not re-terminated
    assert module.instances[1].terminate_count == 0  # second is the live one

    stream.stop()
    stream.close()
    assert module.instances[1].terminate_count == 1  # live instance released exactly once


def test_loopback_start_start_stream_failure_closes_stream_and_terminates():
    # Bug 2: open() succeeds but start_stream() raises -> the opened stream AND the
    # PyAudio instance must both be cleaned up, and the error must propagate.
    module = FakePyAudioModule(fail_start=True)
    stream = _make_stream(module)

    with pytest.raises(RuntimeError, match="start_stream boom"):
        stream.start()

    pa = module.instances[0]
    opened = pa.streams[0]
    assert opened.close_count == 1  # opened stream closed (not leaked)
    assert pa.terminate_count == 1  # PyAudio instance terminated (not leaked)
    assert stream._stream is None
    assert stream._pa is None

    # close() after a failed start is a safe no-op (idempotent, no double-free).
    stream.close()
    assert opened.close_count == 1
    assert pa.terminate_count == 1


def test_loopback_normal_lifecycle_terminates_exactly_once():
    # Regression: a clean start -> stop -> close terminates exactly once, and a second
    # close() must not double-terminate or double-close.
    module = FakePyAudioModule()
    stream = _make_stream(module)

    stream.start()
    pa = module.instances[0]
    opened = pa.streams[0]
    stream.stop()
    stream.close()

    assert opened.close_count == 1  # stream closed exactly once
    assert pa.terminate_count == 1  # terminated exactly once (no double-terminate, no leak)

    stream.close()  # idempotent
    assert opened.close_count == 1
    assert pa.terminate_count == 1


def test_loopback_start_cleanup_close_failure_still_terminates_and_propagates_original():
    # Hole 1: start_stream() raises AND stream.close() ALSO raises during cleanup. The
    # teardown must be best-effort + total: pa.terminate() must STILL run (no leak) and
    # the ORIGINAL start_stream error must propagate, not the secondary close error.
    module = FakePyAudioModule(fail_start=True, fail_close=True)
    stream = _make_stream(module)

    with pytest.raises(RuntimeError, match="start_stream boom"):
        stream.start()

    pa = module.instances[0]
    opened = pa.streams[0]
    assert opened.close_count == 1  # close was attempted...
    assert pa.terminate_count == 1  # ...and terminate STILL ran despite close raising
    assert stream._stream is None
    assert stream._pa is None


def test_loopback_close_terminate_failure_zeros_state_and_allows_clean_restart():
    # Hole 2: close() where terminate() raises after _stream was nulled must NOT leave the
    # object half-torn-down (_pa=set, _stream=None). Teardown must zero BOTH handles so a
    # later start() opens a FRESH instance instead of overwriting/orphaning the stale _pa.
    module = FakePyAudioModule(fail_terminate=True)
    stream = _make_stream(module)

    stream.start()
    first = module.instances[0]
    opened = first.streams[0]

    stream.close()  # terminate() raises, but close() must suppress it (best-effort teardown)

    assert opened.close_count == 1
    assert first.terminate_count == 1  # terminate was attempted
    assert stream._stream is None
    assert stream._pa is None  # state zeroed despite terminate raising

    # A later start() must open a FRESH instance, not orphan the stale (raised) _pa.
    stream.start()
    assert len(module.instances) == 2
    assert module.instances[1].terminate_count == 0  # second is the live, fresh instance


def test_list_loopback_devices_filters_and_maps_dicts():
    infos = [
        {
            "index": 5,
            "name": "Speakers (loopback)",
            "defaultSampleRate": 48000.0,
            "maxInputChannels": 2,
        },
        {
            "index": 6,
            "name": "Disabled (loopback)",
            "defaultSampleRate": 44100.0,
            "maxInputChannels": 0,
        },
    ]

    devices = list_loopback_devices(query_loopback=lambda: infos)

    assert devices == [InputDevice(index=5, name="Speakers (loopback)", channels=2)]


def test_resolve_loopback_device_uses_default_when_spec_blank():
    module = FakePyAudioModule(
        default_info={
            "index": 9,
            "name": "Default Speakers (loopback)",
            "defaultSampleRate": 48000.0,
            "maxInputChannels": 2,
        }
    )

    device = resolve_loopback_device(None, pyaudio_module=module)

    assert device == LoopbackDevice(
        index=9, name="Default Speakers (loopback)", channels=2, samplerate=48000
    )
    assert module.instances[0].terminated is True


def test_resolve_loopback_device_matches_by_index_and_name():
    infos = [
        {
            "index": 3,
            "name": "Game Audio (loopback)",
            "defaultSampleRate": 48000.0,
            "maxInputChannels": 2,
        },
        {
            "index": 4,
            "name": "Chat Audio (loopback)",
            "defaultSampleRate": 44100.0,
            "maxInputChannels": 2,
        },
    ]

    by_index = resolve_loopback_device("4", pyaudio_module=FakePyAudioModule(infos=infos))
    assert by_index == LoopbackDevice(
        index=4, name="Chat Audio (loopback)", channels=2, samplerate=44100
    )

    by_name = resolve_loopback_device("game", pyaudio_module=FakePyAudioModule(infos=infos))
    assert by_name.index == 3


def test_resolve_loopback_device_rejects_unknown_spec():
    infos = [
        {
            "index": 3,
            "name": "Game Audio (loopback)",
            "defaultSampleRate": 48000.0,
            "maxInputChannels": 2,
        }
    ]

    with pytest.raises(ValueError, match="No loopback device"):
        resolve_loopback_device("missing", pyaudio_module=FakePyAudioModule(infos=infos))


def test_make_capture_loopback_raises_on_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    config = AppConfig(audio={"source": "loopback"})

    with pytest.raises(RuntimeError, match="Windows"):
        make_capture(config)


def test_make_capture_loopback_on_windows_selects_native_rate_and_factory(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    module = FakePyAudioModule(
        default_info={
            "index": 7,
            "name": "Speakers (loopback)",
            "defaultSampleRate": 48000.0,
            "maxInputChannels": 2,
        }
    )
    config = AppConfig(audio={"source": "loopback"})

    capture = make_capture(config, pyaudio_module=module)

    assert capture.device == 7
    assert capture.samplerate == 48000
    assert capture.channels == 1
    assert capture._stream_factory is not None


def test_make_capture_mic_path_uses_sounddevice_defaults(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    config = AppConfig()  # audio.source defaults to "mic"

    capture = make_capture(config)

    assert capture.samplerate == 16000
    assert capture.channels == 1
    assert capture._stream_factory is None
