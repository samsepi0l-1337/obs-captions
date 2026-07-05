// SPDX-License-Identifier: GPL-2.0
#pragma once

#include <cstddef>
#include <array>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace obs_native_ipc {

constexpr std::size_t kMaxPayloadBytes = 16 * 1024 * 1024;
constexpr std::uint16_t kProtocolVersion = 1;
constexpr std::array<char, 4> kFrameMagic{'O', 'B', 'S', 'C'};

enum class MessageType : std::uint16_t {
	HELLO = 0x0001,
	READY = 0x0002,
	AUDIO = 0x0003,
	CAPTION_PARTIAL = 0x0004,
	CAPTION_FINAL = 0x0005,
	CONTROL = 0x0006,
	STATUS = 0x0007,
	HEARTBEAT = 0x0008,
	FLUSH_DONE = 0x0009,
};

enum class DecodeStatus {
	Ok,
	NeedMoreData,
	BadMagic,
	BadCrc,
	VersionMismatch,
	UnknownMessageType,
	PayloadTooLarge,
};

struct DecodedFrame {
	MessageType type{MessageType::HEARTBEAT};
	std::vector<std::uint8_t> payload;
};

struct DecodeResult {
	DecodeStatus status{DecodeStatus::NeedMoreData};
	DecodedFrame frame;
	std::size_t bytes_consumed{0};
};

std::uint32_t crc32(const void *data, std::size_t size);

std::vector<std::uint8_t> encode_frame(MessageType type, const std::vector<std::uint8_t> &payload);

DecodeResult try_decode_frame(const std::uint8_t *data, std::size_t size);

DecodeResult try_decode_frame(const std::vector<std::uint8_t> &data);

bool is_known_message_type(std::uint16_t value);

} // namespace obs_native_ipc
