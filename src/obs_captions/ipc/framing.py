from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any
from zlib import crc32


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


@dataclass(frozen=True)
class Audio:
    timestamp_ns: int
    sample_count: int
    samples: list[int]


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


def _ensure_u16(value: int, field: str) -> int:
    if not (0 <= value <= 0xFFFF):
        raise FrameError(f"{field} out of u16 range: {value}")
    return value


def _ensure_i16(value: int, field: str) -> int:
    if not (-0x8000 <= value <= 0x7FFF):
        raise FrameError(f"{field} out of i16 range: {value}")
    return value


def _ensure_u32(value: int, field: str) -> int:
    if not (0 <= value <= 0xFFFFFFFF):
        raise FrameError(f"{field} out of u32 range: {value}")
    return value


def _ensure_u64(value: int, field: str) -> int:
    if not (0 <= value <= 0xFFFFFFFFFFFFFFFF):
        raise FrameError(f"{field} out of u64 range: {value}")
    return value


def _pack_text(value: str) -> bytes:
    data = value.encode("utf-8")
    return struct.pack("<I", len(data)) + data


def _unpack_text(payload: bytes, offset: int) -> tuple[str, int]:
    if offset + 4 > len(payload):
        raise FrameError("Incomplete string length")
    text_len = struct.unpack_from("<I", payload, offset)[0]
    offset += 4
    end = offset + text_len
    if end > len(payload):
        raise FrameError("Incomplete string bytes")
    return payload[offset:end].decode("utf-8"), end


def _pack_frame(msg_type: MsgType, payload: bytes) -> bytes:
    payload_len = len(payload)
    if payload_len > MAX_PAYLOAD_SIZE:
        raise FrameError(f"payload too large: {payload_len}")
    header_wo_crc = struct.pack(
        "<4sHHI",
        MAGIC,
        CURRENT_VERSION,
        int(msg_type),
        payload_len,
    )
    checksum = crc32(header_wo_crc) & 0xFFFFFFFF
    return header_wo_crc + struct.pack("<I", checksum) + payload


def encode_hello(
    proto_version: int,
    epoch: int,
    sample_rate: int,
    channels: int,
    sample_format: int,
    config_path: str,
) -> bytes:
    payload = struct.pack(
        "<HIIHH",
        _ensure_u16(proto_version, "proto_version"),
        _ensure_u32(epoch, "epoch"),
        _ensure_u32(sample_rate, "sample_rate"),
        _ensure_u16(channels, "channels"),
        _ensure_u16(sample_format, "sample_format"),
    ) + _pack_text(config_path)
    return _pack_frame(MsgType.HELLO, payload)


def encode_ready(
    accepted_version: int,
    epoch: int,
    engine_name: str,
    language: str,
    supports_partial: int,
) -> bytes:
    payload = struct.pack(
        "<HI",
        _ensure_u16(accepted_version, "accepted_version"),
        _ensure_u32(epoch, "epoch"),
    ) + _pack_text(engine_name) + _pack_text(language) + struct.pack("<B", supports_partial & 0xFF)
    return _pack_frame(MsgType.READY, payload)


def encode_audio(
    timestamp_ns: int,
    sample_count: int,
    samples: list[int],
) -> bytes:
    sample_count_u32 = _ensure_u32(sample_count, "sample_count")
    if sample_count_u32 != len(samples):
        raise FrameError("sample_count does not match samples length")
    sample_bytes = bytearray()
    for sample in samples:
        sample_bytes += struct.pack("<h", _ensure_i16(sample, "sample"))
    payload = struct.pack(
        "<QI",
        _ensure_u64(timestamp_ns, "timestamp_ns"),
        sample_count_u32,
    ) + bytes(sample_bytes)
    return _pack_frame(MsgType.AUDIO, payload)


def _decode_audio(payload: bytes) -> Audio:
    if len(payload) < 12:
        raise FrameError("Audio payload too small")
    timestamp_ns, sample_count = struct.unpack_from("<QI", payload, 0)
    expected_len = 12 + sample_count * 2
    if len(payload) != expected_len:
        raise FrameError(f"Audio payload length mismatch: {len(payload)} != {expected_len}")
    sample_data = payload[12:]
    samples = list(struct.unpack("<{}h".format(sample_count), sample_data)) if sample_data else []
    return Audio(_ensure_u64(timestamp_ns, "timestamp_ns"), sample_count, samples)


def encode_caption_partial(
    epoch: int,
    timestamp_ns: int,
    seq: int,
    text: str,
) -> bytes:
    payload = struct.pack(
        "<IQQ",
        _ensure_u32(epoch, "epoch"),
        _ensure_u64(timestamp_ns, "timestamp_ns"),
        _ensure_u64(seq, "seq"),
    ) + _pack_text(text)
    return _pack_frame(MsgType.CAPTION_PARTIAL, payload)


def encode_caption_final(
    epoch: int,
    timestamp_ns: int,
    seq: int,
    text: str,
) -> bytes:
    payload = struct.pack(
        "<IQQ",
        _ensure_u32(epoch, "epoch"),
        _ensure_u64(timestamp_ns, "timestamp_ns"),
        _ensure_u64(seq, "seq"),
    ) + _pack_text(text)
    return _pack_frame(MsgType.CAPTION_FINAL, payload)


def _decode_caption(payload: bytes, msg_type: MsgType) -> Any:
    if len(payload) < 24:
        raise FrameError("Caption payload too small")
    epoch, timestamp_ns, seq = struct.unpack_from("<IQQ", payload, 0)
    text, offset = _unpack_text(payload, 20)
    if offset != len(payload):
        raise FrameError("Caption payload contains trailing bytes")
    if msg_type == MsgType.CAPTION_PARTIAL:
        return CaptionPartial(_ensure_u32(epoch, "epoch"), _ensure_u64(timestamp_ns, "timestamp_ns"), _ensure_u64(seq, "seq"), text)
    return CaptionFinal(_ensure_u32(epoch, "epoch"), _ensure_u64(timestamp_ns, "timestamp_ns"), _ensure_u64(seq, "seq"), text)


def encode_control(command: int, seq: int, arg: str) -> bytes:
    payload = struct.pack(
        "<HQ",
        _ensure_u16(command, "command"),
        _ensure_u64(seq, "seq"),
    ) + _pack_text(arg)
    return _pack_frame(MsgType.CONTROL, payload)


def _decode_control(payload: bytes) -> Control:
    if len(payload) < 14:
        raise FrameError("Control payload too small")
    command, seq = struct.unpack_from("<HQ", payload, 0)
    arg, offset = _unpack_text(payload, 10)
    if offset != len(payload):
        raise FrameError("Control payload contains trailing bytes")
    return Control(
        _ensure_u16(command, "command"),
        _ensure_u64(seq, "seq"),
        arg,
    )


def encode_status(code: int | StatusCode, ack_seq: int, message: str) -> bytes:
    code_u16 = int(code)
    if code_u16 not in {int(x) for x in StatusCode}:
        raise FrameError(f"Unsupported status code: {code}")
    payload = struct.pack(
        "<HQ",
        _ensure_u16(code_u16, "status_code"),
        _ensure_u64(ack_seq, "ack_seq"),
    ) + _pack_text(message)
    return _pack_frame(MsgType.STATUS, payload)


def _decode_status(payload: bytes) -> Status:
    if len(payload) < 14:
        raise FrameError("Status payload too small")
    code, ack_seq = struct.unpack_from("<HQ", payload, 0)
    if code not in {int(x) for x in StatusCode}:
        raise FrameError(f"Unknown status code: {code}")
    message, offset = _unpack_text(payload, 10)
    if offset != len(payload):
        raise FrameError("Status payload contains trailing bytes")
    return Status(code, _ensure_u64(ack_seq, "ack_seq"), message)


def encode_heartbeat() -> bytes:
    return _pack_frame(MsgType.HEARTBEAT, b"")


def encode_flush_done(seq: int) -> bytes:
    payload = struct.pack("<Q", _ensure_u64(seq, "seq"))
    return _pack_frame(MsgType.FLUSH_DONE, payload)


def _decode_ready(payload: bytes) -> Ready:
    if len(payload) < 15:
        raise FrameError("Ready payload too small")
    accepted_version, epoch = struct.unpack_from("<HI", payload, 0)
    engine_name, offset = _unpack_text(payload, 6)
    language, offset = _unpack_text(payload, offset)
    if offset + 1 > len(payload):
        raise FrameError("Ready payload truncated")
    supports_partial = payload[offset]
    if offset + 1 != len(payload):
        raise FrameError("Ready payload contains trailing bytes")
    return Ready(
        _ensure_u16(accepted_version, "accepted_version"),
        _ensure_u32(epoch, "epoch"),
        engine_name,
        language,
        supports_partial,
    )


def _decode_hello(payload: bytes) -> Hello:
    if len(payload) < 18:
        raise FrameError("Hello payload too small")
    proto_version, epoch, sample_rate, channels, sample_format = struct.unpack_from(
        "<HIIHH", payload, 0
    )
    config_path, offset = _unpack_text(payload, 14)
    if offset != len(payload):
        raise FrameError("Hello payload contains trailing bytes")
    return Hello(
        _ensure_u16(proto_version, "proto_version"),
        _ensure_u32(epoch, "epoch"),
        _ensure_u32(sample_rate, "sample_rate"),
        _ensure_u16(channels, "channels"),
        _ensure_u16(sample_format, "sample_format"),
        config_path,
    )


def _decode_flush_done(payload: bytes) -> FlushDone:
    if len(payload) != 8:
        raise FrameError(f"FlushDone payload length invalid: {len(payload)}")
    seq = struct.unpack_from("<Q", payload, 0)[0]
    return FlushDone(_ensure_u64(seq, "seq"))


def _decode_payload(msg_type: MsgType, payload: bytes) -> Any:
    if msg_type == MsgType.HELLO:
        return _decode_hello(payload)
    if msg_type == MsgType.READY:
        return _decode_ready(payload)
    if msg_type == MsgType.AUDIO:
        return _decode_audio(payload)
    if msg_type == MsgType.CAPTION_PARTIAL:
        return _decode_caption(payload, msg_type)
    if msg_type == MsgType.CAPTION_FINAL:
        return _decode_caption(payload, msg_type)
    if msg_type == MsgType.CONTROL:
        return _decode_control(payload)
    if msg_type == MsgType.STATUS:
        return _decode_status(payload)
    if msg_type == MsgType.HEARTBEAT:
        if len(payload) != 0:
            raise FrameError("Heartbeat payload must be empty")
        return Heartbeat()
    if msg_type == MsgType.FLUSH_DONE:
        return _decode_flush_done(payload)
    raise FrameError(f"Unknown msg_type {msg_type}")


def decode_frame(buffer: bytes) -> tuple[int, MsgType, Any]:
    if len(buffer) < HEADER_SIZE:
        raise NeedMoreData("Incomplete header")
    magic, version, msg_type_raw, payload_len, header_crc = struct.unpack(
        HEADER_STRUCT,
        buffer[:HEADER_SIZE],
    )
    if magic != MAGIC:
        raise FrameError("magic mismatch")
    if version != CURRENT_VERSION:
        raise FrameError(f"unsupported version: {version}")
    try:
        msg_type = MsgType(msg_type_raw)
    except ValueError as exc:
        raise FrameError(f"unknown msg_type: {msg_type_raw}") from exc
    if payload_len > MAX_PAYLOAD_SIZE:
        raise FrameError(f"payload length too large: {payload_len}")
    expected_header_crc = crc32(buffer[:12]) & 0xFFFFFFFF
    if header_crc != expected_header_crc:
        raise FrameError("header crc mismatch")
    total_len = HEADER_SIZE + payload_len
    if len(buffer) < total_len:
        raise NeedMoreData("Incomplete payload")
    payload = buffer[HEADER_SIZE:total_len]
    message = _decode_payload(msg_type, payload)
    return total_len, msg_type, message


class FrameDecoder:
    """Stateful stream decoder for concatenated IPC frames.

    NeedMoreData means the caller can provide more bytes.
    FrameError means protocol/format violation; the decoder trims consumed bytes and
    should be discarded/reinitialized by the caller after the exception.
    """

    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, data: bytes) -> list[tuple[MsgType, Any]]:
        self._buffer += data
        out: list[tuple[MsgType, Any]] = []
        cursor = 0
        while True:
            try:
                consumed, msg_type, payload = decode_frame(self._buffer[cursor:])
            except NeedMoreData:
                break
            except FrameError:
                self._buffer = self._buffer[cursor:]
                raise
            out.append((msg_type, payload))
            cursor += consumed
            if cursor >= len(self._buffer):
                break
        self._buffer = self._buffer[cursor:]
        return out


__all__ = [
    "FrameError",
    "NeedMoreData",
    "MsgType",
    "StatusCode",
    "MAX_PAYLOAD_SIZE",
    "MAGIC",
    "CURRENT_VERSION",
    "Hello",
    "Ready",
    "Audio",
    "CaptionPartial",
    "CaptionFinal",
    "Control",
    "Status",
    "Heartbeat",
    "FlushDone",
    "FrameDecoder",
    "decode_frame",
    "encode_hello",
    "encode_ready",
    "encode_audio",
    "encode_caption_partial",
    "encode_caption_final",
    "encode_control",
    "encode_status",
    "encode_heartbeat",
    "encode_flush_done",
]
