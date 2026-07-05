// SPDX-License-Identifier: GPL-2.0
#include "framing.hpp"

#include <cstdint>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

namespace {

void append_u16(std::vector<std::uint8_t> &buf, std::uint16_t value)
{
	buf.push_back(static_cast<std::uint8_t>(value & 0xffu));
	buf.push_back(static_cast<std::uint8_t>((value >> 8) & 0xffu));
}

void append_u32(std::vector<std::uint8_t> &buf, std::uint32_t value)
{
	buf.push_back(static_cast<std::uint8_t>(value & 0xffu));
	buf.push_back(static_cast<std::uint8_t>((value >> 8) & 0xffu));
	buf.push_back(static_cast<std::uint8_t>((value >> 16) & 0xffu));
	buf.push_back(static_cast<std::uint8_t>((value >> 24) & 0xffu));
}

void append_u64(std::vector<std::uint8_t> &buf, std::uint64_t value)
{
	buf.push_back(static_cast<std::uint8_t>(value & 0xffu));
	buf.push_back(static_cast<std::uint8_t>((value >> 8) & 0xffu));
	buf.push_back(static_cast<std::uint8_t>((value >> 16) & 0xffu));
	buf.push_back(static_cast<std::uint8_t>((value >> 24) & 0xffu));
	buf.push_back(static_cast<std::uint8_t>((value >> 32) & 0xffu));
	buf.push_back(static_cast<std::uint8_t>((value >> 40) & 0xffu));
	buf.push_back(static_cast<std::uint8_t>((value >> 48) & 0xffu));
	buf.push_back(static_cast<std::uint8_t>((value >> 56) & 0xffu));
}

std::vector<std::uint8_t> make_control_payload(std::uint16_t command, std::uint64_t seq, std::string arg)
{
	std::vector<std::uint8_t> payload;
	append_u16(payload, command);
	append_u64(payload, seq);
	append_u32(payload, static_cast<std::uint32_t>(arg.size()));
	payload.insert(payload.end(), arg.begin(), arg.end());
	return payload;
}

std::vector<std::uint8_t> make_status_payload(std::uint16_t code, std::uint64_t ack_seq, std::string msg)
{
	std::vector<std::uint8_t> payload;
	append_u16(payload, code);
	append_u64(payload, ack_seq);
	append_u32(payload, static_cast<std::uint32_t>(msg.size()));
	payload.insert(payload.end(), msg.begin(), msg.end());
	return payload;
}

std::vector<std::uint8_t> make_caption_payload(std::uint32_t epoch, std::uint64_t ts, std::uint64_t seq,
					      bool is_final, std::string text)
{
	const auto &type = is_final ? obs_native_ipc::MessageType::CAPTION_FINAL
				   : obs_native_ipc::MessageType::CAPTION_PARTIAL;
	(void)type;
	std::vector<std::uint8_t> payload;
	append_u32(payload, epoch);
	append_u64(payload, ts);
	append_u64(payload, seq);
	append_u32(payload, static_cast<std::uint32_t>(text.size()));
	payload.insert(payload.end(), text.begin(), text.end());
	return payload;
}

std::vector<std::uint8_t> make_audio_payload(std::uint64_t ts, const std::vector<std::int16_t> &samples)
{
	std::vector<std::uint8_t> payload;
	append_u64(payload, ts);
	append_u32(payload, static_cast<std::uint32_t>(samples.size()));
	for (std::uint16_t sample : samples) {
		payload.push_back(static_cast<std::uint8_t>(sample & 0xffu));
		payload.push_back(static_cast<std::uint8_t>((sample >> 8) & 0xffu));
	}
	return payload;
}

std::vector<std::uint8_t> make_flush_payload(std::uint64_t seq)
{
	std::vector<std::uint8_t> payload;
	append_u64(payload, seq);
	return payload;
}

void assert_true(bool cond, const char *message)
{
	if (!cond) {
		std::cerr << "FAILED: " << message << std::endl;
		std::exit(1);
	}
}

} // namespace

int main()
{
	using namespace obs_native_ipc;

	const auto control_payload = make_control_payload(1, 1001u, "hello");
	const auto status_payload = make_status_payload(0, 2001u, "ok");
	const auto final_payload = make_caption_payload(1, 100u, 77u, true, "final text");
	const auto partial_payload = make_caption_payload(1, 101u, 78u, false, "partial text");
	const auto audio_payload = make_audio_payload(12u, {1, 2, 3, 4});
	const auto flush_payload = make_flush_payload(300u);

	const auto control_frame = encode_frame(MessageType::CONTROL, control_payload);
	const auto status_frame = encode_frame(MessageType::STATUS, status_payload);
	const auto flush_frame = encode_frame(MessageType::FLUSH_DONE, flush_payload);
	const auto final_frame = encode_frame(MessageType::CAPTION_FINAL, final_payload);
	const auto partial_frame = encode_frame(MessageType::CAPTION_PARTIAL, partial_payload);
	const auto audio_frame = encode_frame(MessageType::AUDIO, audio_payload);

	const auto decode_and_check = [&](MessageType type, const std::vector<std::uint8_t> &frame,
					  const std::vector<std::uint8_t> &expected_payload) {
		const auto result = try_decode_frame(frame);
		assert_true(result.status == DecodeStatus::Ok, "decode should succeed");
		assert_true(result.frame.type == type, "message type mismatch");
		assert_true(result.bytes_consumed == frame.size(), "consumed bytes mismatch");
		assert_true(result.frame.payload == expected_payload, "payload mismatch");
	};

	decode_and_check(MessageType::CONTROL, control_frame, control_payload);
	decode_and_check(MessageType::STATUS, status_frame, status_payload);
	decode_and_check(MessageType::FLUSH_DONE, flush_frame, flush_payload);
	decode_and_check(MessageType::CAPTION_FINAL, final_frame, final_payload);
	decode_and_check(MessageType::CAPTION_PARTIAL, partial_frame, partial_payload);
	decode_and_check(MessageType::AUDIO, audio_frame, audio_payload);

	const auto short_header = std::vector<std::uint8_t>{0x4f, 0x42};
	auto short_result = try_decode_frame(short_header);
	assert_true(short_result.status == DecodeStatus::NeedMoreData, "partial header should request more data");

	const auto partial_payload_frame = audio_frame;
	const auto need_more = try_decode_frame(partial_payload_frame.data(), 18);
	assert_true(need_more.status == DecodeStatus::NeedMoreData, "partial payload should request more data");

	const auto too_large = std::vector<std::uint8_t>(kMaxPayloadBytes + 1, 0x42);
	bool threw = false;
	try {
		std::ignore = encode_frame(MessageType::STATUS, too_large);
	} catch (const std::length_error &) {
		threw = true;
	}
	assert_true(threw, "payload length oversize should throw");

	auto embed_payload = std::vector<std::uint8_t>(control_payload);
	embed_payload.insert(embed_payload.end(), {'O', 'B', 'S', 'C', 'X'});
	const auto embed_frame = encode_frame(MessageType::CONTROL, embed_payload);
	const auto embed_result = try_decode_frame(embed_frame);
	assert_true(embed_result.status == DecodeStatus::Ok, "embedded OBSC should not break framing");
	assert_true(embed_result.frame.payload == embed_payload, "embedded payload should decode raw");

	std::vector<std::uint8_t> bad_magic = control_frame;
	bad_magic[0] = 'X';
	auto bad_magic_result = try_decode_frame(bad_magic);
	assert_true(bad_magic_result.status == DecodeStatus::BadMagic, "magic mismatch should fail");

	std::vector<std::uint8_t> bad_crc = control_frame;
	bad_crc[10] ^= 0xffu;
	auto bad_crc_result = try_decode_frame(bad_crc);
	assert_true(bad_crc_result.status == DecodeStatus::BadCrc, "crc mismatch should fail");

	std::vector<std::uint8_t> version_mismatch = control_frame;
	version_mismatch[4] = 0x02;
	version_mismatch[5] = 0x00;
	const auto version_crc = crc32(version_mismatch.data(), 12);
	version_mismatch[12] = static_cast<std::uint8_t>(version_crc & 0xffu);
	version_mismatch[13] = static_cast<std::uint8_t>((version_crc >> 8) & 0xffu);
	version_mismatch[14] = static_cast<std::uint8_t>((version_crc >> 16) & 0xffu);
	version_mismatch[15] = static_cast<std::uint8_t>((version_crc >> 24) & 0xffu);
	auto version_result = try_decode_frame(version_mismatch);
	assert_true(version_result.status == DecodeStatus::VersionMismatch, "version mismatch should fail");

	std::vector<std::uint8_t> unknown_type = control_frame;
	unknown_type[6] = 0xffu;
	unknown_type[7] = 0xffu;
	const auto unknown_crc = crc32(unknown_type.data(), 12);
	unknown_type[12] = static_cast<std::uint8_t>(unknown_crc & 0xffu);
	unknown_type[13] = static_cast<std::uint8_t>((unknown_crc >> 8) & 0xffu);
	unknown_type[14] = static_cast<std::uint8_t>((unknown_crc >> 16) & 0xffu);
	unknown_type[15] = static_cast<std::uint8_t>((unknown_crc >> 24) & 0xffu);
	auto unknown_result = try_decode_frame(unknown_type);
	assert_true(unknown_result.status == DecodeStatus::UnknownMessageType, "unknown type should fail");

	const auto bad_eof = status_frame;
	auto no_payload = try_decode_frame(bad_eof.data(), bad_eof.size() - 1);
	assert_true(no_payload.status == DecodeStatus::NeedMoreData, "EOF short payload should fail incomplete");

	std::cout << "framing_test: PASS" << std::endl;
	return 0;
}
