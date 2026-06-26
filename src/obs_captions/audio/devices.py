from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    channels: int


def list_input_devices(
    *,
    query_devices: Callable[[], Sequence[dict[str, Any]]] | None = None,
) -> list[InputDevice]:
    query = query_devices or _sounddevice_query_devices
    devices: list[InputDevice] = []
    for index, device in enumerate(query()):
        channels = int(device.get("max_input_channels", 0))
        if channels > 0:
            devices.append(
                InputDevice(index=index, name=str(device.get("name", "")), channels=channels)
            )
    return devices


def list_loopback_devices(
    *,
    query_loopback: Callable[[], Sequence[dict[str, Any]]] | None = None,
) -> list[InputDevice]:
    """List WASAPI loopback (system-audio) devices on Windows.

    Injectable like :func:`list_input_devices`; the default seam lazy-imports
    ``loopback.query_loopback_devices`` so importing this module never pulls in
    ``pyaudiowpatch`` (Windows-only).
    """
    query = query_loopback or _pyaudiowpatch_query_loopback
    devices: list[InputDevice] = []
    for info in query():
        channels = int(info.get("maxInputChannels", 0))
        if channels > 0:
            devices.append(
                InputDevice(
                    index=int(info.get("index", 0)),
                    name=str(info.get("name", "")),
                    channels=channels,
                )
            )
    return devices


def resolve_device(
    spec: str | int | None,
    *,
    devices: Sequence[InputDevice] | None = None,
    query_devices: Callable[[], Sequence[dict[str, Any]]] | None = None,
) -> int | None:
    if spec is None or str(spec).strip() == "":
        return None

    available = (
        list(devices) if devices is not None else list_input_devices(query_devices=query_devices)
    )
    text = str(spec).strip()
    if text.isdecimal():
        index = int(text)
        if any(device.index == index for device in available):
            return index
        raise ValueError(f"No input device index: {index}")

    matches = [device for device in available if text.lower() in device.name.lower()]
    if not matches:
        raise ValueError(f"No input device matching: {spec}")
    if len(matches) > 1:
        names = ", ".join(f"{device.index}:{device.name}" for device in matches)
        raise ValueError(f"Ambiguous input device {spec!r}: {names}")
    return matches[0].index


def _sounddevice_query_devices() -> Sequence[dict[str, Any]]:
    import sounddevice as sd

    return sd.query_devices()


def _pyaudiowpatch_query_loopback() -> Sequence[dict[str, Any]]:
    from obs_captions.audio.loopback import query_loopback_devices

    return query_loopback_devices()
