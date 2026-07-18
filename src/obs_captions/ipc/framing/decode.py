from __future__ import annotations

import struct
from typing import Any
from zlib import crc32

from .messages import (
    CURRENT_VERSION,
    HEADER_SIZE,
    HEADER_STRUCT,
    MAGIC,
    MAX_PAYLOAD_SIZE,
    Audio,
    CaptionFinal,
    CaptionPartial,
    Control,
    FlushDone,
    FrameError,
    Heartbeat,
    Hello,
    MsgType,
    NeedMoreData,
    Ready,
    Status,
    StatusCode,
    _ensure_u16,
    _ensure_u32,
    _ensure_u64,
)


def _unpack_text(payload: bytes, offset: int) -> tuple[str, int]:
    if offset + 4 > len(payload):
        raise FrameError("Incomplete string length")
    text_len = struct.unpack_from("<I", payload, offset)[0]
    offset += 4
    end = offset + text_len
    if end > len(payload):
        raise FrameError("Incomplete string bytes")
    return payload[offset:end].decode("utf-8"), end


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


def _decode_audio(payload: bytes) -> Audio:
    if len(payload) < 12:
        raise FrameError("Audio payload too small")
    timestamp_ns, sample_count = struct.unpack_from("<QI", payload, 0)
    expected_len = 12 + sample_count * 2
    if len(payload) != expected_len:
        raise FrameError(f"Audio payload length mismatch: {len(payload)} != {expected_len}")
    return Audio.from_pcm(
        _ensure_u64(timestamp_ns, "timestamp_ns"),
        sample_count,
        payload[12:],
    )


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


def decode_frame(buffer: bytes, offset: int = 0) -> tuple[int, MsgType, Any]:
    available = len(buffer) - offset
    if available < HEADER_SIZE:
        raise NeedMoreData("Incomplete header")
    magic, version, msg_type_raw, payload_len, header_crc = struct.unpack_from(
        HEADER_STRUCT,
        buffer,
        offset,
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
    expected_header_crc = crc32(buffer[offset:offset + 12]) & 0xFFFFFFFF
    if header_crc != expected_header_crc:
        raise FrameError("header crc mismatch")
    total_len = HEADER_SIZE + payload_len
    if available < total_len:
        raise NeedMoreData("Incomplete payload")
    payload_start = offset + HEADER_SIZE
    payload = buffer[payload_start:offset + total_len]
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
        buffer = self._buffer
        total = len(buffer)
        cursor = 0
        while cursor < total:
            try:
                consumed, msg_type, payload = decode_frame(buffer, cursor)
            except NeedMoreData:
                break
            except FrameError:
                self._buffer = buffer[cursor:]
                raise
            out.append((msg_type, payload))
            cursor += consumed
        self._buffer = buffer[cursor:]
        return out
