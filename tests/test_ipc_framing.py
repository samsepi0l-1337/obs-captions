from __future__ import annotations

import struct
import zlib

import pytest

from obs_captions.ipc.framing import (
    FrameDecoder,
    FrameError,
    NeedMoreData,
    MsgType,
    StatusCode,
    decode_frame,
    encode_audio,
    encode_caption_final,
    encode_caption_partial,
    encode_control,
    encode_flush_done,
    encode_heartbeat,
    encode_hello,
    encode_ready,
    encode_status,
)


def _build_bad_len_header(msg_type: MsgType, payload_len: int) -> bytes:
    header_wo_crc = struct.pack("<4sHHI", b"OBSC", 1, int(msg_type), payload_len)
    header_crc = struct.pack("<I", zlib.crc32(header_wo_crc) & 0xFFFFFFFF)
    return header_wo_crc + header_crc


def test_roundtrip_each_message_type():
    cases = (
        (
            MsgType.HELLO,
            dict(
                proto_version=1,
                epoch=1001,
                sample_rate=48_000,
                channels=2,
                sample_format=3,
                config_path="/tmp/config.yaml",
            ),
            encode_hello(
                proto_version=1,
                epoch=1001,
                sample_rate=48_000,
                channels=2,
                sample_format=3,
                config_path="/tmp/config.yaml",
            ),
        ),
        (
            MsgType.READY,
            dict(
                accepted_version=1,
                epoch=1002,
                engine_name="local",
                language="en",
                supports_partial=1,
            ),
            encode_ready(
                accepted_version=1,
                epoch=1002,
                engine_name="local",
                language="en",
                supports_partial=1,
            ),
        ),
        (
            MsgType.AUDIO,
            dict(
                timestamp_ns=1003,
                sample_count=5,
                samples=[1, -2, 300, 0, -400],
            ),
            encode_audio(
                timestamp_ns=1003,
                sample_count=5,
                samples=[1, -2, 300, 0, -400],
            ),
        ),
        (
            MsgType.CAPTION_PARTIAL,
            dict(
                epoch=1004,
                timestamp_ns=1005,
                seq=7,
                text="hello",
            ),
            encode_caption_partial(
                epoch=1004,
                timestamp_ns=1005,
                seq=7,
                text="hello",
            ),
        ),
        (
            MsgType.CAPTION_FINAL,
            dict(
                epoch=1006,
                timestamp_ns=1007,
                seq=8,
                text="world",
            ),
            encode_caption_final(
                epoch=1006,
                timestamp_ns=1007,
                seq=8,
                text="world",
            ),
        ),
        (
            MsgType.CONTROL,
            dict(
                command=2,
                seq=9,
                arg="flush",
            ),
            encode_control(
                command=2,
                seq=9,
                arg="flush",
            ),
        ),
        (
            MsgType.STATUS,
            dict(
                code=StatusCode.OK,
                ack_seq=12,
                message="running",
            ),
            encode_status(
                code=StatusCode.OK,
                ack_seq=12,
                message="running",
            ),
        ),
        (MsgType.HEARTBEAT, {}, encode_heartbeat()),
        (
            MsgType.FLUSH_DONE,
            dict(seq=13),
            encode_flush_done(seq=13),
        ),
    )

    for expected_type, expected_fields, frame in cases:
        consumed, actual_type, payload = decode_frame(frame)
        assert consumed == len(frame)
        assert actual_type == expected_type
        for field, expected_value in expected_fields.items():
            assert getattr(payload, field) == expected_value


def test_status_code_roundtrip_for_all_codes():
    for code in range(8):
        frame = encode_status(code=code, ack_seq=99, message=f"code-{code}")
        _, msg_type, payload = decode_frame(frame)
        assert msg_type == MsgType.STATUS
        assert payload.code == code


def test_header_crc_mismatch_rejected():
    frame = encode_hello(
        proto_version=1,
        epoch=11,
        sample_rate=16_000,
        channels=1,
        sample_format=1,
        config_path="/x",
    )
    corrupted = bytearray(frame)
    corrupted[1] ^= 0xFF

    with pytest.raises(FrameError):
        decode_frame(bytes(corrupted))


def test_payload_len_over_limit_is_rejected():
    bad_frame = _build_bad_len_header(MsgType.HELLO, payload_len=16 * 1024 * 1024 + 1)
    with pytest.raises(FrameError):
        decode_frame(bad_frame)


def test_unknown_msg_type_is_rejected():
    header_wo_crc = struct.pack("<4sHHI", b"OBSC", 1, 0xFF, 0)
    header_crc = struct.pack("<I", zlib.crc32(header_wo_crc) & 0xFFFFFFFF)
    frame = header_wo_crc + header_crc
    with pytest.raises(FrameError):
        decode_frame(frame)


def test_status_code_out_of_range_rejected():
    with pytest.raises(FrameError):
        encode_status(code=8, ack_seq=1, message="invalid")

    payload = struct.pack("<HQI", 8, 99, 0)
    frame = _build_bad_len_header(MsgType.STATUS, len(payload)) + payload
    with pytest.raises(FrameError):
        decode_frame(frame)


def test_malformed_complete_payload_raises_frame_error():
    payload = b"\x00" * 5
    frame = _build_bad_len_header(MsgType.HELLO, len(payload)) + payload
    with pytest.raises(FrameError):
        decode_frame(frame)

    decoder = FrameDecoder()
    with pytest.raises(FrameError):
        decoder.feed(frame)


def test_feed_frame_error_trims_consumed_bytes_and_contracts_teardown():
    good = encode_heartbeat()
    malformed = _build_bad_len_header(MsgType.HELLO, 5) + b"\x00" * 5
    decoder = FrameDecoder()

    with pytest.raises(FrameError):
        decoder.feed(good + malformed)

    assert decoder._buffer == malformed

def test_embedded_magic_in_payload_does_not_resync():
    frame = encode_caption_partial(
        epoch=1,
        timestamp_ns=2,
        seq=3,
        text="contains OBSC marker inside payload: OBSC",
    )
    frames = FrameDecoder().feed(frame)
    assert len(frames) == 1
    msg_type, payload = frames[0]
    assert msg_type == MsgType.CAPTION_PARTIAL
    assert payload.text == "contains OBSC marker inside payload: OBSC"


def test_partial_frame_and_recovery_after_need_more_data():
    frame = encode_caption_final(
        epoch=55,
        timestamp_ns=66,
        seq=77,
        text="recovery",
    )

    decoder = FrameDecoder()
    with pytest.raises(NeedMoreData):
        decode_frame(frame[:5])
    assert decoder.feed(frame[:7]) == []

    recovered = decoder.feed(frame[7:])
    assert len(recovered) == 1
    assert recovered[0][0] == MsgType.CAPTION_FINAL
    assert recovered[0][1].text == "recovery"


def test_version_mismatch_rejected():
    frame = encode_heartbeat()
    corrupted = bytearray(frame)
    struct.pack_into("<H", corrupted, 4, 2)
    corrupted[12:16] = struct.pack(
        "<I", zlib.crc32(bytes(corrupted[:12])) & 0xFFFFFFFF
    )
    with pytest.raises(FrameError):
        decode_frame(bytes(corrupted))
