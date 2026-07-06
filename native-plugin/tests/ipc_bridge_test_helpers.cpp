// SPDX-License-Identifier: GPL-2.0

#include "ipc_bridge_test_helpers.hpp"
#include "framing.hpp"

#include <chrono>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#else
#include <unistd.h>
#endif

using namespace std::chrono_literals;

void assert_true(bool cond, const char *message)
{
	if (!cond) {
		std::cerr << "FAILED: " << message << std::endl;
		std::exit(1);
	}
}

std::uint16_t read_u16(const std::vector<std::uint8_t> &data, std::size_t off)
{
	return static_cast<std::uint16_t>(static_cast<std::uint16_t>(data[off]) |
					 (static_cast<std::uint16_t>(data[off + 1u]) << 8u));
}

std::uint32_t read_u32(const std::vector<std::uint8_t> &data, std::size_t off)
{
	return static_cast<std::uint32_t>(static_cast<std::uint32_t>(data[off]) |
					 (static_cast<std::uint32_t>(data[off + 1u]) << 8u) |
					 (static_cast<std::uint32_t>(data[off + 2u]) << 16u) |
					 (static_cast<std::uint32_t>(data[off + 3u]) << 24u));
}

std::uint64_t read_u64(const std::vector<std::uint8_t> &data, std::size_t off)
{
	return static_cast<std::uint64_t>(static_cast<std::uint64_t>(data[off])) |
	       (static_cast<std::uint64_t>(data[off + 1u]) << 8u) |
	       (static_cast<std::uint64_t>(data[off + 2u]) << 16u) |
	       (static_cast<std::uint64_t>(data[off + 3u]) << 24u) |
	       (static_cast<std::uint64_t>(data[off + 4u]) << 32u) |
	       (static_cast<std::uint64_t>(data[off + 5u]) << 40u) |
	       (static_cast<std::uint64_t>(data[off + 6u]) << 48u) |
	       (static_cast<std::uint64_t>(data[off + 7u]) << 56u);
}

void push_u16(std::vector<std::uint8_t> &out, std::uint16_t v)
{
	out.push_back(static_cast<std::uint8_t>(v & 0xffu));
	out.push_back(static_cast<std::uint8_t>((v >> 8u) & 0xffu));
}

void push_u32(std::vector<std::uint8_t> &out, std::uint32_t v)
{
	out.push_back(static_cast<std::uint8_t>(v & 0xffu));
	out.push_back(static_cast<std::uint8_t>((v >> 8u) & 0xffu));
	out.push_back(static_cast<std::uint8_t>((v >> 16u) & 0xffu));
	out.push_back(static_cast<std::uint8_t>((v >> 24u) & 0xffu));
}

void push_u64(std::vector<std::uint8_t> &out, std::uint64_t v)
{
	out.push_back(static_cast<std::uint8_t>(v & 0xffu));
	out.push_back(static_cast<std::uint8_t>((v >> 8u) & 0xffu));
	out.push_back(static_cast<std::uint8_t>((v >> 16u) & 0xffu));
	out.push_back(static_cast<std::uint8_t>((v >> 24u) & 0xffu));
	out.push_back(static_cast<std::uint8_t>((v >> 32u) & 0xffu));
	out.push_back(static_cast<std::uint8_t>((v >> 40u) & 0xffu));
	out.push_back(static_cast<std::uint8_t>((v >> 48u) & 0xffu));
	out.push_back(static_cast<std::uint8_t>((v >> 56u) & 0xffu));
}

bool write_all_stdout(const std::vector<std::uint8_t> &data)
{
	if (data.empty()) {
		return true;
	}
	const std::size_t n = std::fwrite(data.data(), 1u, data.size(), stdout);
	std::fflush(stdout);
	return n == data.size();
}

bool parse_ready(const std::vector<std::uint8_t> &payload, std::uint16_t &version, std::uint32_t &epoch)
{
	if (payload.size() < 13u) {
		return false;
	}
	version = read_u16(payload, 0u);
	epoch = read_u32(payload, 2u);
	const auto engine_len = read_u32(payload, 6u);
	if (payload.size() < 10u + engine_len + 4u) {
		return false;
	}
	const auto lang_len = read_u32(payload, 10u + engine_len);
	if (payload.size() != static_cast<std::size_t>(15u + engine_len + lang_len)) {
		return false;
	}
	return true;
}

bool parse_hello(const std::vector<std::uint8_t> &payload, std::uint16_t &version, std::uint32_t &epoch)
{
	if (payload.size() < 14u) {
		return false;
	}
	version = read_u16(payload, 0u);
	epoch = read_u32(payload, 2u);
	const auto sample_rate = read_u32(payload, 6u);
	const auto channels = read_u16(payload, 10u);
	const auto sample_format = read_u16(payload, 12u);
	if (sample_rate != 16000u || channels != 1u || sample_format != 1u) {
		return false;
	}
	const auto path_len = read_u32(payload, 14u);
	if (payload.size() != 18u + path_len) {
		return false;
	}
	return true;
}

bool parse_control(const std::vector<std::uint8_t> &payload, std::uint16_t &command, std::uint64_t &seq)
{
	if (payload.size() < 14u) {
		return false;
	}
	command = read_u16(payload, 0u);
	seq = read_u64(payload, 2u);
	const auto arg_len = read_u32(payload, 10u);
	if (payload.size() < 14u + arg_len) {
		return false;
	}
	return true;
}

struct FakeDecodedFrame {
	obs_native_ipc::MessageType type{obs_native_ipc::MessageType::HEARTBEAT};
	std::vector<std::uint8_t> payload;
	std::size_t bytes_consumed{0u};
};

bool parse_fake_frame(std::vector<std::uint8_t> &in, FakeDecodedFrame &out)
{
	const bool debug = std::getenv("OBS_BRIDGE_TEST_DEBUG") != nullptr;
	const auto result = obs_native_ipc::try_decode_frame(in);
	switch (result.status) {
	case obs_native_ipc::DecodeStatus::Ok:
		out.type = result.frame.type;
		out.payload = std::move(result.frame.payload);
		out.bytes_consumed = result.bytes_consumed;
		in.erase(in.begin(), in.begin() + static_cast<std::ptrdiff_t>(result.bytes_consumed));
		return true;
	case obs_native_ipc::DecodeStatus::NeedMoreData:
		return false;
	case obs_native_ipc::DecodeStatus::BadMagic:
	case obs_native_ipc::DecodeStatus::BadCrc:
	case obs_native_ipc::DecodeStatus::VersionMismatch:
	case obs_native_ipc::DecodeStatus::PayloadTooLarge:
	case obs_native_ipc::DecodeStatus::UnknownMessageType:
		if (debug) {
			std::cerr << "parse_fake_frame status=" << static_cast<int>(result.status) << " size=" << in.size()
				  << " first=" << std::hex << static_cast<int>(in.front()) << ' ' << static_cast<int>(in[1]) << ' '
				  << static_cast<int>(in[2]) << ' ' << static_cast<int>(in[3]) << std::dec << '\n';
		}
		if (!in.empty()) {
			in.erase(in.begin());
		}
		return false;
	}
	return false;
}

bool send_ready(std::uint32_t epoch)
{
	std::vector<std::uint8_t> payload;
	push_u16(payload, 1u); // protocol version
	push_u32(payload, epoch);
	const std::string engine = "test-engine";
	const std::string language = "en";
	push_u32(payload, static_cast<std::uint32_t>(engine.size()));
	payload.insert(payload.end(), engine.begin(), engine.end());
	push_u32(payload, static_cast<std::uint32_t>(language.size()));
	payload.insert(payload.end(), language.begin(), language.end());
	payload.push_back(static_cast<std::uint8_t>(1)); // supports_partial
	const auto frame = obs_native_ipc::encode_frame(obs_native_ipc::MessageType::READY, payload);
	return write_all_stdout(frame);
}

bool send_status(std::uint16_t code, std::uint64_t seq, const char *msg)
{
	std::vector<std::uint8_t> payload;
	push_u16(payload, code);
	push_u64(payload, seq);
	const std::string txt = msg ? msg : "";
	push_u32(payload, static_cast<std::uint32_t>(txt.size()));
	payload.insert(payload.end(), txt.begin(), txt.end());
	const auto frame = obs_native_ipc::encode_frame(obs_native_ipc::MessageType::STATUS, payload);
	return write_all_stdout(frame);
}

bool send_heartbeat()
{
	const auto frame = obs_native_ipc::encode_frame(obs_native_ipc::MessageType::HEARTBEAT, {});
	return write_all_stdout(frame);
}

bool send_stale_caption(std::uint32_t epoch, const std::string &text)
{
	std::vector<std::uint8_t> payload;
	push_u32(payload, epoch);
	push_u64(payload, 123u);
	push_u64(payload, 999u);
	push_u32(payload, static_cast<std::uint32_t>(text.size()));
	payload.insert(payload.end(), text.begin(), text.end());
	const auto frame = obs_native_ipc::encode_frame(obs_native_ipc::MessageType::CAPTION_FINAL, payload);
	return write_all_stdout(frame);
}

std::FILE *child_log_file()
{
	static std::FILE *fp = nullptr;
	static bool initialized = false;
	if (!initialized) {
		initialized = true;
		const char *path = std::getenv("OBS_BRIDGE_TEST_CHILD_LOG");
		if (path && path[0] != '\0') {
			fp = std::fopen(path, "a");
		} else {
			fp = reinterpret_cast<std::FILE *>(-1);
		}
	}
	return (fp == reinterpret_cast<std::FILE *>(-1)) ? nullptr : fp;
}

void child_log(const char *message)
{
	if (std::FILE *fp = child_log_file()) {
		std::fputs(message, fp);
		std::fputc('\n', fp);
		std::fflush(fp);
	}
}

obs_native_ipc::SpawnConfig fake_config(const char *argv0, const char *mode)
{
	return obs_native_ipc::SpawnConfig{{argv0, "--fake-child", mode}};
}

int read_input(std::vector<std::uint8_t> &buffer, std::vector<std::uint8_t> &in, bool &eof)
{
#ifdef _WIN32
	const int bytes = _read(_fileno(stdin), in.data(), static_cast<unsigned int>(in.size()));
#else
	const auto bytes = read(STDIN_FILENO, in.data(), in.size());
#endif
	if (bytes <= 0) {
		eof = bytes == 0;
		return eof ? 0 : -1;
	}

	const auto n = static_cast<std::size_t>(bytes);
	buffer.insert(buffer.end(), in.data(), in.data() + n);
	return static_cast<int>(n);
}

int run_fake_child(const std::string &mode)
{
	using namespace obs_native_ipc;
	if (mode == "exit_before_ready") {
		return 0;
	}
	const bool debug = std::getenv("OBS_BRIDGE_TEST_DEBUG") != nullptr;
	std::vector<std::uint8_t> in_buf;
	in_buf.reserve(4096u);
	std::vector<std::uint8_t> read_buf(4096u);
	bool saw_first_hello = false;
	bool sent_stale = false;
	std::uint32_t last_epoch = 0u;
	std::size_t heartbeat_count = 0u;

		while (true) {
			bool eof = false;
			const int bytes = read_input(in_buf, read_buf, eof);
			if (bytes <= 0) {
				child_log("fake_child bytes_eof");
				if (debug) {
					std::cerr << "fake_child bytes=" << bytes << " eof=" << eof << std::endl;
				}
				return eof ? 0 : 1;
			}
			char msg_buf[64];
			snprintf(msg_buf, sizeof(msg_buf), "fake_child bytes_%d", bytes);
			child_log(msg_buf);
				while (true) {
					FakeDecodedFrame decoded_frame;
					if (!parse_fake_frame(in_buf, decoded_frame)) {
						child_log("fake_child decode_parse_retry");
						if (in_buf.size() < 16u) {
							child_log("fake_child decode_need_more");
							break;
						}
						continue;
					}
				child_log("fake_child decode_ok");
				const auto &f = decoded_frame;
				switch (f.type) {
				case MessageType::HELLO: {
					std::uint16_t version = 0u;
					std::uint32_t epoch = 0u;
					if (!parse_hello(f.payload, version, epoch)) {
						if (debug) {
							std::cerr << "fake_child parse_hello_failed payload=" << f.payload.size() << std::endl;
						}
						child_log("fake_child parse_hello_failed");
						return 1;
					}
					last_epoch = epoch;
					if (debug) {
						std::cerr << "fake_child hello version=" << version << " epoch=" << epoch << " payload="
							  << f.payload.size() << std::endl;
					}
					child_log("fake_child hello_parsed");
					if (!send_ready(epoch)) {
						if (debug) {
							std::cerr << "fake_child send_ready_failed" << std::endl;
						}
						child_log("fake_child send_ready_failed");
						return 1;
					}
					child_log("fake_child send_ready_ok");
					if (mode == "heartbeat_restart") {
						if (saw_first_hello) {
							if (!sent_stale) {
								const std::uint32_t stale_epoch = epoch > 0u ? epoch - 1u : 0u;
								sent_stale = true;
								if (!send_stale_caption(stale_epoch, "stale-caption")) {
									child_log("fake_child send_stale_failed");
									return 1;
								}
								child_log("fake_child send_stale_ok");
							}
						}
					}
				saw_first_hello = true;
				break;
			}
			case MessageType::CONTROL: {
				std::uint16_t command = 0u;
				std::uint64_t seq = 0u;
				if (!parse_control(f.payload, command, seq)) {
					return 1;
				}
				switch (command) {
				case 1u:
				case 2u:
				case 3u:
				case 4u:
					if (!send_status(0u, seq, "ok")) {
						return 1;
					}
					break;
				default:
					break;
				}
				break;
			}
			case MessageType::HEARTBEAT:
				if (mode != "heartbeat_restart" || heartbeat_count < 2u) {
					if (mode == "heartbeat_restart") {
						++heartbeat_count;
					}
					if (!send_heartbeat()) {
						return 1;
					}
				}
				break;
			default:
				break;
			}
		}
		if (mode == "heartbeat_restart") {
			(void)last_epoch;
		}
		if (mode == "wedge") {
			// Keep process alive while parent drives control/heartbeat timeouts.
			std::this_thread::sleep_for(20ms);
		}
	}
}
