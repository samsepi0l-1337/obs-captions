from __future__ import annotations

import struct
from zlib import crc32

from .messages import (
    CURRENT_VERSION,
    MAGIC,
    MAX_PAYLOAD_SIZE,
    FrameError,
    MsgType,
    StatusCode,
    _ensure_u16,
    _ensure_u32,
    _ensure_u64,
)


def _pack_text(value: str) -> bytes:
    data = value.encode("utf-8")
    return struct.pack("<I", len(data)) + data


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
    try:
        sample_bytes = struct.pack("<%dh" % len(samples), *samples)
    except struct.error as exc:
        raise FrameError(f"invalid audio sample: {exc}") from exc
    payload = struct.pack(
        "<QI",
        _ensure_u64(timestamp_ns, "timestamp_ns"),
        sample_count_u32,
    ) + sample_bytes
    return _pack_frame(MsgType.AUDIO, payload)


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


def encode_control(command: int, seq: int, arg: str) -> bytes:
    payload = struct.pack(
        "<HQ",
        _ensure_u16(command, "command"),
        _ensure_u64(seq, "seq"),
    ) + _pack_text(arg)
    return _pack_frame(MsgType.CONTROL, payload)


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


def encode_heartbeat() -> bytes:
    return _pack_frame(MsgType.HEARTBEAT, b"")


def encode_flush_done(seq: int) -> bytes:
    payload = struct.pack("<Q", _ensure_u64(seq, "seq"))
    return _pack_frame(MsgType.FLUSH_DONE, payload)
