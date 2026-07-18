from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum


MAGIC = b"OBSC"
CURRENT_VERSION = 1
MAX_PAYLOAD_SIZE = 16 * 1024 * 1024
HEADER_SIZE = 16
HEADER_STRUCT = "<4sHHII"


class FrameError(ValueError):
    """Base error for IPC framing and payload parse failures."""


class NeedMoreData(FrameError):
    """Raised when a complete frame has not yet arrived."""


class MsgType(IntEnum):
    HELLO = 0x01
    READY = 0x02
    AUDIO = 0x03
    CAPTION_PARTIAL = 0x04
    CAPTION_FINAL = 0x05
    CONTROL = 0x06
    STATUS = 0x07
    HEARTBEAT = 0x08
    FLUSH_DONE = 0x09


class StatusCode(IntEnum):
    OK = 0
    ENGINE_INIT_FAIL = 1
    RUNTIME_ERROR = 2
    CONFIG_ERROR = 3
    FATAL = 4
    SUPERSEDED = 5
    CANCELLED = 6
    NO_SESSION = 7


def _ensure_u16(value: int, field: str) -> int:
    if not (0 <= value <= 0xFFFF):
        raise FrameError(f"{field} out of u16 range: {value}")
    return value


def _ensure_u32(value: int, field: str) -> int:
    if not (0 <= value <= 0xFFFFFFFF):
        raise FrameError(f"{field} out of u32 range: {value}")
    return value


def _ensure_u64(value: int, field: str) -> int:
    if not (0 <= value <= 0xFFFFFFFFFFFFFFFF):
        raise FrameError(f"{field} out of u64 range: {value}")
    return value


@dataclass(frozen=True)
class Hello:
    proto_version: int
    epoch: int
    sample_rate: int
    channels: int
    sample_format: int
    config_path: str


@dataclass(frozen=True)
class Ready:
    accepted_version: int
    epoch: int
    engine_name: str
    language: str
    supports_partial: int


class Audio:
    """Decoded AUDIO message.

    Stores the raw little-endian PCM16 payload bytes so the consumer hot path
    can use ``pcm`` directly without an int-list round-trip. ``samples`` stays
    available for backwards compatibility and is derived lazily (unpacked on
    first access) and cached.

    Construct via the raw-bytes path (``Audio.from_pcm`` / ``pcm=...``) on the
    decode hot path; the legacy positional form ``Audio(ts, count, samples)``
    remains supported and packs the samples into ``pcm`` eagerly.
    """

    __slots__ = ("timestamp_ns", "sample_count", "pcm", "_samples_cache")

    def __init__(
        self,
        timestamp_ns: int,
        sample_count: int,
        samples: list[int] | None = None,
        *,
        pcm: bytes | None = None,
    ) -> None:
        self.timestamp_ns = timestamp_ns
        self.sample_count = sample_count
        if pcm is not None:
            self.pcm = pcm
            self._samples_cache: list[int] | None = None
        else:
            resolved = list(samples) if samples is not None else []
            self.pcm = struct.pack("<%dh" % len(resolved), *resolved) if resolved else b""
            self._samples_cache = resolved

    @classmethod
    def from_pcm(cls, timestamp_ns: int, sample_count: int, pcm: bytes) -> Audio:
        return cls(timestamp_ns, sample_count, pcm=pcm)

    @classmethod
    def from_samples(cls, timestamp_ns: int, sample_count: int, samples: list[int]) -> Audio:
        return cls(timestamp_ns, sample_count, samples)

    @property
    def samples(self) -> list[int]:
        cache = self._samples_cache
        if cache is None:
            cache = (
                list(struct.unpack("<%dh" % self.sample_count, self.pcm))
                if self.pcm
                else []
            )
            self._samples_cache = cache
        return cache

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Audio):
            return NotImplemented
        return (
            self.timestamp_ns == other.timestamp_ns
            and self.sample_count == other.sample_count
            and self.pcm == other.pcm
        )

    def __repr__(self) -> str:
        return (
            f"Audio(timestamp_ns={self.timestamp_ns}, "
            f"sample_count={self.sample_count}, samples={self.samples})"
        )


@dataclass(frozen=True)
class CaptionPartial:
    epoch: int
    timestamp_ns: int
    seq: int
    text: str


@dataclass(frozen=True)
class CaptionFinal:
    epoch: int
    timestamp_ns: int
    seq: int
    text: str


@dataclass(frozen=True)
class Control:
    command: int
    seq: int
    arg: str


@dataclass(frozen=True)
class Status:
    code: int
    ack_seq: int
    message: str


@dataclass(frozen=True)
class Heartbeat:
    pass


@dataclass(frozen=True)
class FlushDone:
    seq: int
