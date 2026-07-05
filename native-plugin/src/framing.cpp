// SPDX-License-Identifier: GPL-2.0
#include "framing.hpp"

#include <algorithm>
#include <array>
#include <cstring>
#include <stdexcept>

namespace {

constexpr std::size_t kHeaderSize = 16;

void write_u16_le(std::uint8_t *out, std::uint16_t value)
{
	out[0] = static_cast<std::uint8_t>(value & 0xffu);
	out[1] = static_cast<std::uint8_t>((value >> 8) & 0xffu);
}

void write_u32_le(std::uint8_t *out, std::uint32_t value)
{
	out[0] = static_cast<std::uint8_t>(value & 0xffu);
	out[1] = static_cast<std::uint8_t>((value >> 8) & 0xffu);
	out[2] = static_cast<std::uint8_t>((value >> 16) & 0xffu);
	out[3] = static_cast<std::uint8_t>((value >> 24) & 0xffu);
}

std::uint16_t read_u16_le(const std::uint8_t *in)
{
	return static_cast<std::uint16_t>(static_cast<std::uint16_t>(in[0]) |
					 (static_cast<std::uint16_t>(in[1]) << 8));
}

std::uint32_t read_u32_le(const std::uint8_t *in)
{
	return static_cast<std::uint32_t>(static_cast<std::uint32_t>(in[0]) |
					 (static_cast<std::uint32_t>(in[1]) << 8) |
					 (static_cast<std::uint32_t>(in[2]) << 16) |
					 (static_cast<std::uint32_t>(in[3]) << 24));
}

std::array<std::uint32_t, 256> make_crc_table()
{
	std::array<std::uint32_t, 256> table{};
	for (std::uint32_t i = 0; i < 256; ++i) {
		std::uint32_t crc = i;
		for (int bit = 0; bit < 8; ++bit) {
			crc = (crc & 1u) ? (0xEDB88320u ^ (crc >> 1u)) : (crc >> 1u);
		}
		table[i] = crc;
	}
	return table;
}

const std::array<std::uint32_t, 256> kCrcTable = make_crc_table();

} // namespace

namespace obs_native_ipc {

std::uint32_t crc32(const void *data, std::size_t size)
{
	const auto *bytes = static_cast<const std::uint8_t *>(data);
	std::uint32_t crc = 0xFFFFFFFFu;

	for (std::size_t i = 0; i < size; ++i) {
		const std::uint8_t index = static_cast<std::uint8_t>(crc ^ bytes[i]);
		crc = kCrcTable[index] ^ (crc >> 8u);
	}

	return crc ^ 0xFFFFFFFFu;
}

bool is_known_message_type(std::uint16_t value)
{
	switch (static_cast<MessageType>(value)) {
	case MessageType::HELLO:
	case MessageType::READY:
	case MessageType::AUDIO:
	case MessageType::CAPTION_PARTIAL:
	case MessageType::CAPTION_FINAL:
	case MessageType::CONTROL:
	case MessageType::STATUS:
	case MessageType::HEARTBEAT:
	case MessageType::FLUSH_DONE:
		return true;
	default:
		return false;
	}
}

std::vector<std::uint8_t> encode_frame(MessageType type, const std::vector<std::uint8_t> &payload)
{
	if (!is_known_message_type(static_cast<std::uint16_t>(type))) {
		throw std::invalid_argument("unknown message type");
	}

	if (payload.size() > kMaxPayloadBytes) {
		throw std::length_error("payload too large");
	}

	std::vector<std::uint8_t> output(kHeaderSize + payload.size());
	std::copy(kFrameMagic.begin(), kFrameMagic.end(), output.data());
	write_u16_le(output.data() + 4, kProtocolVersion);
	write_u16_le(output.data() + 6, static_cast<std::uint16_t>(type));
	write_u32_le(output.data() + 8, static_cast<std::uint32_t>(payload.size()));
	write_u32_le(output.data() + 12, 0u);

	const std::uint32_t header_crc = crc32(output.data(), kHeaderSize - 4);
	write_u32_le(output.data() + 12, header_crc);

	std::copy(payload.begin(), payload.end(), output.begin() + kHeaderSize);
	return output;
}

DecodeResult try_decode_frame(const std::uint8_t *data, std::size_t size)
{
	DecodeResult result;

	if (size < kHeaderSize) {
		result.status = DecodeStatus::NeedMoreData;
		return result;
	}

	if (std::memcmp(data, kFrameMagic.data(), kFrameMagic.size()) != 0) {
		result.status = DecodeStatus::BadMagic;
		return result;
	}

	const std::uint16_t version = read_u16_le(data + 4);
	const std::uint16_t msg_type = read_u16_le(data + 6);
	const std::uint32_t payload_len = read_u32_le(data + 8);
	const std::uint32_t header_crc = read_u32_le(data + 12);
	const std::uint32_t expected_crc = crc32(data, kHeaderSize - 4);

	if (header_crc != expected_crc) {
		result.status = DecodeStatus::BadCrc;
		return result;
	}

	if (version != kProtocolVersion) {
		result.status = DecodeStatus::VersionMismatch;
		return result;
	}

	if (payload_len > kMaxPayloadBytes) {
		result.status = DecodeStatus::PayloadTooLarge;
		return result;
	}

	if (!is_known_message_type(msg_type)) {
		result.status = DecodeStatus::UnknownMessageType;
		return result;
	}

	const std::size_t full_size = kHeaderSize + static_cast<std::size_t>(payload_len);
	if (size < full_size) {
		result.status = DecodeStatus::NeedMoreData;
		return result;
	}

	result.status = DecodeStatus::Ok;
	result.frame.type = static_cast<MessageType>(msg_type);
	result.frame.payload.assign(data + kHeaderSize, data + full_size);
	result.bytes_consumed = full_size;
	return result;
}

DecodeResult try_decode_frame(const std::vector<std::uint8_t> &data)
{
	return try_decode_frame(data.data(), data.size());
}

} // namespace obs_native_ipc
