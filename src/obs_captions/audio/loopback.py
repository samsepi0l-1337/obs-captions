"""Windows WASAPI loopback (desktop/system-audio) capture.

This module is the *only* place that touches ``pyaudiowpatch`` (a PyAudio fork),
and it lazy-imports it so the package keeps importing on macOS/Linux where the
dependency is absent.

The :class:`LoopbackStream` adapter bridges the two API mismatches between
PyAudioWPatch and the ``sounddevice``-style stream that :class:`MicCapture`
expects:

* lifecycle: ``start()/stop()/close()`` -> ``start_stream()/stop_stream()/
  close()`` (+ ``PyAudio.terminate()``).
* callback/format: PyAudio delivers ``in_data: bytes`` (paFloat32) ->
  reshaped to ``np.ndarray`` of shape ``(frames, channels)`` and forwarded as
  ``callback(indata, frames, time, status)``.

Opening in ``paFloat32`` keeps MicCapture's existing float32 -> mono ->
resample -> PCM16 pipeline correct; the native (e.g. 48k stereo) device frames
are downmixed and resampled to 16k mono by MicCapture, unchanged.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

_INSTALL_HINT = (
    "pyaudiowpatch is required for loopback capture; "
    "install with `uv sync --extra loopback` (Windows only)"
)


@dataclass(frozen=True)
class LoopbackDevice:
    index: int
    name: str
    channels: int
    samplerate: int


def _import_pyaudiowpatch() -> Any:
    try:
        import pyaudiowpatch
    except ImportError as exc:  # pragma: no cover - exercised only off Windows
        raise ImportError(_INSTALL_HINT) from exc
    return pyaudiowpatch


class LoopbackStream:
    """``sounddevice``-style adapter over a PyAudioWPatch loopback stream."""

    def __init__(
        self,
        *,
        samplerate: int,
        device_channels: int,
        blocksize: int,
        device: int | None,
        callback: Callable[[np.ndarray, int, Any, Any], None],
        pyaudio_module: Any | None = None,
    ) -> None:
        self._samplerate = int(samplerate)
        self._channels = int(device_channels)
        self._blocksize = int(blocksize)
        self._device = device
        self._callback = callback
        self._pyaudio_module = pyaudio_module
        self._pa: Any | None = None
        self._stream: Any | None = None
        self._continue: Any = None

    def start(self) -> None:
        # Single source of truth for "already started": either native handle being
        # held means start() is a no-op. Guarding on both (not just _stream) means a
        # half-state can never let a retry overwrite/orphan a live _pa.
        if self._pa is not None or self._stream is not None:
            return
        module = self._pyaudio_module or _import_pyaudiowpatch()
        self._continue = module.paContinue
        pa = module.PyAudio()
        stream = None
        try:
            stream = pa.open(
                format=module.paFloat32,
                channels=self._channels,
                rate=self._samplerate,
                input=True,
                input_device_index=self._device,
                frames_per_buffer=self._blocksize,
                stream_callback=self._on_data,
            )
            stream.start_stream()
        except Exception:
            # Any failure (open or start_stream) must release the native handles via the
            # one best-effort teardown, then re-raise the ORIGINAL error -- a teardown
            # error must not mask it. After _dispose both handles are None, so a later
            # close() / retry stays a safe no-op (no orphan, no leak).
            self._dispose(stream, pa)
            raise
        self._pa = pa
        self._stream = stream

    def _dispose(self, stream: Any, pa: Any) -> None:
        """Best-effort, total teardown shared by start()'s failure path and close().

        Attempt BOTH ``stream.close()`` and ``pa.terminate()`` regardless of either
        raising -- teardown errors are suppressed because we are already unwinding --
        and ALWAYS null both handles so the adapter returns to a clean, restartable
        state even if ``terminate()`` raises.
        """
        if stream is not None:
            with contextlib.suppress(Exception):
                stream.close()
        if pa is not None:
            with contextlib.suppress(Exception):
                pa.terminate()
        self._stream = None
        self._pa = None

    def _on_data(self, in_data: bytes, frame_count: int, time_info: Any, status: Any) -> tuple:
        array = np.frombuffer(in_data, dtype=np.float32).reshape(-1, self._channels)
        self._callback(array, frame_count, time_info, status)
        return (None, self._continue)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop_stream()

    def close(self) -> None:
        # Deliberate teardown: best-effort + total via the shared helper. close() never
        # raises and always zeroes both handles, so it is idempotent and a later start()
        # reopens cleanly even if terminate() raised here.
        self._dispose(self._stream, self._pa)


def make_loopback_stream_factory(
    *,
    device_channels: int,
    pyaudio_module: Any | None = None,
) -> Callable[..., LoopbackStream]:
    """Build a stream factory matching MicCapture's ``stream_factory`` seam.

    MicCapture forces its logical ``channels``/``dtype`` to mono float32; the
    native device channel count (``device_channels``) is captured here so the
    PyAudio stream opens with the device's real layout and MicCapture downmixes.
    """

    def factory(
        *,
        samplerate: int,
        blocksize: int,
        device: int | None,
        callback: Callable[[np.ndarray, int, Any, Any], None],
        channels: int | None = None,
        dtype: str | None = None,
    ) -> LoopbackStream:
        _ = channels, dtype  # logical mono pipeline; device opens with native channels
        return LoopbackStream(
            samplerate=samplerate,
            device_channels=device_channels,
            blocksize=blocksize,
            device=device,
            callback=callback,
            pyaudio_module=pyaudio_module,
        )

    return factory


def query_loopback_devices(*, pyaudio_module: Any | None = None) -> list[dict[str, Any]]:
    """Enumerate WASAPI loopback device info dicts (default seam for devices.py)."""
    module = pyaudio_module or _import_pyaudiowpatch()
    pa = module.PyAudio()
    try:
        return list(pa.get_loopback_device_info_generator())
    finally:
        pa.terminate()


def resolve_loopback_device(
    spec: str | int | None,
    *,
    pyaudio_module: Any | None = None,
) -> LoopbackDevice:
    """Resolve ``spec`` to a loopback device, including its native sample rate.

    ``None``/empty selects the default WASAPI loopback; otherwise ``spec`` is an
    index or a case-insensitive name substring (mirrors ``resolve_device``).
    """
    module = pyaudio_module or _import_pyaudiowpatch()
    pa = module.PyAudio()
    try:
        text = "" if spec is None else str(spec).strip()
        if text == "":
            info = pa.get_default_wasapi_loopback()
            if info is None:
                raise ValueError("No default WASAPI loopback device found")
        else:
            info = _match_loopback_info(pa.get_loopback_device_info_generator(), text)
    finally:
        pa.terminate()
    return _device_from_info(info)


def _match_loopback_info(infos: Iterable[dict[str, Any]], text: str) -> dict[str, Any]:
    available = list(infos)
    if text.isdecimal():
        index = int(text)
        for info in available:
            if int(info.get("index", -1)) == index:
                return info
        raise ValueError(f"No loopback device index: {index}")

    matches = [info for info in available if text.lower() in str(info.get("name", "")).lower()]
    if not matches:
        raise ValueError(f"No loopback device matching: {text}")
    if len(matches) > 1:
        names = ", ".join(f"{int(info['index'])}:{info.get('name', '')}" for info in matches)
        raise ValueError(f"Ambiguous loopback device {text!r}: {names}")
    return matches[0]


def _device_from_info(info: dict[str, Any]) -> LoopbackDevice:
    return LoopbackDevice(
        index=int(info["index"]),
        name=str(info.get("name", "")),
        channels=int(info.get("maxInputChannels", 0)),
        samplerate=int(round(float(info.get("defaultSampleRate", 0)))),
    )


__all__: Sequence[str] = (
    "LoopbackDevice",
    "LoopbackStream",
    "make_loopback_stream_factory",
    "query_loopback_devices",
    "resolve_loopback_device",
)
