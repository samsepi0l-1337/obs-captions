from __future__ import annotations

import asyncio

import numpy as np
import pytest

from obs_captions.audio.capture import (
    MicCapture,
    float32_to_pcm16,
    pcm16_to_float32,
    resample_linear,
)
from obs_captions.audio.devices import InputDevice, resolve_device


def test_float32_to_pcm16_clips_and_scales_little_endian():
    samples = np.array([-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0], dtype=np.float32)

    pcm = float32_to_pcm16(samples)

    assert np.frombuffer(pcm, dtype="<i2").tolist() == [
        -32768,
        -32768,
        -16384,
        0,
        16383,
        32767,
        32767,
    ]


def test_pcm16_to_float32_round_trips_shape_and_range():
    pcm = np.array([-32768, 0, 32767], dtype="<i2").tobytes()

    samples = pcm16_to_float32(pcm)

    assert samples.dtype == np.float32
    assert samples.tolist() == pytest.approx([-1.0, 0.0, 32767 / 32768])


def test_resample_linear_changes_length_and_preserves_edges():
    source = np.array([0.0, 1.0, 0.0, -1.0], dtype=np.float32)

    resampled = resample_linear(source, source_rate=4, target_rate=8)

    assert len(resampled) == 8
    assert resampled[0] == pytest.approx(0.0)
    assert resampled[-1] == pytest.approx(-1.0)


def test_resample_linear_same_rate_returns_float32_copy():
    source = np.array([0.25, -0.25], dtype=np.float64)

    resampled = resample_linear(source, source_rate=16000, target_rate=16000)

    assert resampled.dtype == np.float32
    assert resampled.tolist() == pytest.approx([0.25, -0.25])
    assert resampled is not source


def test_resolve_device_none_index_and_name_substring():
    devices = [
        InputDevice(index=0, name="MacBook Speakers", channels=0),
        InputDevice(index=1, name="MacBook Microphone", channels=1),
        InputDevice(index=2, name="USB Mic", channels=2),
    ]

    assert resolve_device(None, devices=devices) is None
    assert resolve_device("", devices=devices) is None
    assert resolve_device("2", devices=devices) == 2
    assert resolve_device("micro", devices=devices) == 1


def test_resolve_device_rejects_missing_or_ambiguous_specs():
    devices = [
        InputDevice(index=1, name="USB Mic A", channels=1),
        InputDevice(index=2, name="USB Mic B", channels=1),
    ]

    with pytest.raises(ValueError, match="No input device"):
        resolve_device("missing", devices=devices)
    with pytest.raises(ValueError, match="Ambiguous input device"):
        resolve_device("usb", devices=devices)


@pytest.mark.asyncio
async def test_mic_capture_callback_hands_pcm_to_async_queue_thread_safely():
    callbacks = []

    class FakeStream:
        def __init__(self, **kwargs):
            callbacks.append(kwargs["callback"])
            self.started = False
            self.stopped = False
            self.closed = False

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

        def close(self):
            self.closed = True

    capture = MicCapture(stream_factory=FakeStream, blocksize=2)
    capture.start()
    callbacks[0](np.array([[0.0], [1.0]], dtype=np.float32), 2, None, None)

    frame = await asyncio.wait_for(capture.read(), timeout=1)
    await capture.stop()

    assert np.frombuffer(frame, dtype="<i2").tolist() == [0, 32767]


@pytest.mark.asyncio
async def test_mic_capture_drops_oldest_frame_when_queue_is_full():
    capture = MicCapture(queue_maxsize=1)

    capture._put_nowait_drop_oldest(b"old")
    capture._put_nowait_drop_oldest(b"new")

    assert await asyncio.wait_for(capture.read(), timeout=1) == b"new"
