// SPDX-License-Identifier: GPL-2.0-or-later
#include "ipc-bridge.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <mutex>
#include <vector>

namespace {

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

constexpr std::uint16_t kProtocolVersion = 1u;
constexpr std::uint16_t kSampleFormatI16 = 1u;
constexpr std::uint32_t kSampleRate = 16000u;

} // namespace

namespace obs_native_ipc {

bool IpcBridge::send_hello(std::uint32_t session_epoch)
{
	const bool debug = std::getenv("OBS_BRIDGE_TEST_DEBUG") != nullptr;
	std::vector<std::uint8_t> payload;
	append_u16(payload, kProtocolVersion);
	append_u32(payload, session_epoch);
	append_u32(payload, kSampleRate);
	append_u16(payload, 1u);
	append_u16(payload, kSampleFormatI16);
	append_u32(payload, static_cast<std::uint32_t>(config_.config_path.size()));
	payload.insert(payload.end(), config_.config_path.begin(), config_.config_path.end());
	const auto frame = encode_frame(MessageType::HELLO, payload);
	if (debug) {
		std::cerr << "send_hello frame_size=" << frame.size() << " payload=" << payload.size() << "\n";
		std::cerr << std::hex;
		for (std::size_t i = 0; i < frame.size() && i < 24u; ++i) {
			std::cerr << static_cast<int>(frame[i]) << (i + 1u < frame.size() && i + 1u < 24u ? ' ' : '\n');
		}
		std::cerr << std::dec;
	}
	return write_exact(frame.data(), frame.size());
}

bool IpcBridge::wait_for_ready(std::uint32_t session_epoch)
{
	std::unique_lock lock(state_mutex_);
	const bool ok = ready_cv_.wait_for(lock, config_.hello_timeout, [this, session_epoch]() {
		return desired() != Desired::Run ||
		       (session_ready_.load(std::memory_order_acquire) &&
			session_epoch_.load(std::memory_order_acquire) == session_epoch) ||
		       !running_.load(std::memory_order_acquire);
	});
	return ok && running_.load(std::memory_order_acquire) &&
	       desired() == Desired::Run &&
	       session_ready_.load(std::memory_order_acquire) &&
	       session_epoch_.load(std::memory_order_acquire) == session_epoch;
}

bool IpcBridge::write_exact(const std::uint8_t *data, std::size_t size)
{
	const bool debug = std::getenv("OBS_BRIDGE_TEST_DEBUG") != nullptr;
	std::lock_guard lock(transport_write_mutex_);
	const auto ok = transport_.write_all(data, size);
	if (debug && !ok) {
		std::cerr << "write_exact failed size=" << size << "\n";
	}
	return ok;
}

void IpcBridge::process_payload(const DecodedFrame &frame, std::uint32_t reader_epoch)
{
	const bool debug = std::getenv("OBS_BRIDGE_TEST_DEBUG") != nullptr;
	if (debug) {
		std::lock_guard lock(epoch_mutex_);
		std::cerr << "process_payload type=" << static_cast<int>(frame.type) << " reader_epoch=" << reader_epoch
			  << " active=" << epoch_gate_.active_epoch() << "\n";
	}

	{
		std::lock_guard lock(epoch_mutex_);
		if (!epoch_gate_.should_apply(reader_epoch)) {
			if (debug) {
				std::cerr << "epoch gate mismatch skip\n";
			}
			return;
		}
	}

	switch (frame.type) {
	case MessageType::READY: {
		std::uint16_t accepted_version = 0u;
		std::uint32_t epoch = 0u;
		bool supports_partial = false;
		if (!parse_ready_payload(frame.payload, accepted_version, epoch, supports_partial)) {
			if (debug) {
				std::cerr << "ready parse failed\n";
			}
			request_restart();
			return;
		}
		(void)supports_partial;
		if (epoch != reader_epoch) {
			if (debug) {
				std::cerr << "ready epoch mismatch\n";
			}
			return;
		}
		if (accepted_version != kProtocolVersion) {
			if (debug) {
				std::cerr << "ready version mismatch\n";
			}
			request_restart();
			return;
		}
		set_ready_state(true);
		return;
	}
	case MessageType::HEARTBEAT:
		set_last_heartbeat_now();
		return;
	case MessageType::STATUS: {
		std::uint16_t code = 0u;
		std::uint64_t seq = 0u;
		std::string msg;
		if (!parse_status_payload(frame.payload, code, seq, msg)) {
			request_restart();
			return;
		}
		(void)msg;
		apply_status(code, seq);
		return;
	}
	case MessageType::FLUSH_DONE: {
		if (frame.payload.size() < 8u) {
			request_restart();
			return;
		}
		const auto seq = read_u64(frame.payload, 0u);
		apply_flush_done(seq);
		return;
	}
	case MessageType::CAPTION_PARTIAL:
	case MessageType::CAPTION_FINAL: {
		std::uint32_t epoch = 0u;
		std::uint64_t ts = 0u;
		std::uint64_t seq = 0u;
		std::string text;
		bool is_final = frame.type == MessageType::CAPTION_FINAL;
		if (!parse_caption_payload(frame.payload, epoch, ts, seq, is_final, text)) {
			request_restart();
			return;
		}
		if (epoch != reader_epoch) {
			return;
		}
		apply_caption(epoch, ts, seq, is_final, text);
		return;
	}
	default:
		return;
	}
}

void IpcBridge::apply_status(std::uint16_t code, std::uint64_t seq)
{
	const auto reason = status_reason(code);
	const bool start_complete = control_queue_.notify(ControlCommand::Start, seq, static_cast<WaitReason>(reason));
	const bool stop_complete = control_queue_.notify(ControlCommand::Stop, seq, static_cast<WaitReason>(reason));
	const bool flush_complete = control_queue_.notify(ControlCommand::Flush, seq, static_cast<WaitReason>(reason));
	(void)flush_complete;
	const bool reload_complete = control_queue_.notify(ControlCommand::Reconfigure, seq, static_cast<WaitReason>(reason));
	(void)start_complete;
	(void)stop_complete;
	(void)reload_complete;

	if (code != 0u && code <= 4u) {
		request_restart();
	}
}

void IpcBridge::apply_caption(std::uint32_t epoch, std::uint64_t ts, std::uint64_t seq, bool is_final,
			      const std::string &text)
{
	CaptionEvent evt{epoch, seq, ts, text, is_final};
	{
		std::lock_guard lock(epoch_mutex_);
		if (epoch_gate_.evaluate(evt) != CaptionDecision::Accept) {
			return;
		}
	}

	CaptionCallback cb;
	{
		std::lock_guard lock(callback_mutex_);
		cb = caption_callback_;
	}
	if (cb) {
		cb(evt);
	}
}

void IpcBridge::apply_flush_done(std::uint64_t seq)
{
	control_queue_.notify(ControlCommand::Flush, seq, WaitReason::Complete);
}

void IpcBridge::set_last_heartbeat_now()
{
	last_heartbeat_ns_.store(now_ns(), std::memory_order_release);
}

void IpcBridge::append_u16(std::vector<std::uint8_t> &out, std::uint16_t value)
{
	out.push_back(static_cast<std::uint8_t>(value & 0xffu));
	out.push_back(static_cast<std::uint8_t>((value >> 8u) & 0xffu));
}

void IpcBridge::append_u32(std::vector<std::uint8_t> &out, std::uint32_t value)
{
	out.push_back(static_cast<std::uint8_t>(value & 0xffu));
	out.push_back(static_cast<std::uint8_t>((value >> 8u) & 0xffu));
	out.push_back(static_cast<std::uint8_t>((value >> 16u) & 0xffu));
	out.push_back(static_cast<std::uint8_t>((value >> 24u) & 0xffu));
}

void IpcBridge::append_u64(std::vector<std::uint8_t> &out, std::uint64_t value)
{
	out.push_back(static_cast<std::uint8_t>(value & 0xffu));
	out.push_back(static_cast<std::uint8_t>((value >> 8u) & 0xffu));
	out.push_back(static_cast<std::uint8_t>((value >> 16u) & 0xffu));
	out.push_back(static_cast<std::uint8_t>((value >> 24u) & 0xffu));
	out.push_back(static_cast<std::uint8_t>((value >> 32u) & 0xffu));
	out.push_back(static_cast<std::uint8_t>((value >> 40u) & 0xffu));
	out.push_back(static_cast<std::uint8_t>((value >> 48u) & 0xffu));
	out.push_back(static_cast<std::uint8_t>((value >> 56u) & 0xffu));
}

bool IpcBridge::parse_ready_payload(const std::vector<std::uint8_t> &payload, std::uint16_t &accepted_version,
				   std::uint32_t &epoch, bool &supports_partial)
{
	if (payload.size() < 13u) {
		return false;
	}

	std::size_t off = 0u;
	accepted_version = read_u16(payload, off);
	off += 2u;
	epoch = read_u32(payload, off);
	off += 4u;

	const auto engine_len = read_u32(payload, off);
	off += 4u;
	if (payload.size() < off + engine_len + 5u) {
		return false;
	}
	off += engine_len;

	const auto lang_len = read_u32(payload, off);
	off += 4u;
	if (payload.size() < off + lang_len + 1u) {
		return false;
	}
	if (payload.size() != off + lang_len + 1u) {
		return false;
	}
	supports_partial = (payload[off] != 0u);
	return true;
}

bool IpcBridge::parse_status_payload(const std::vector<std::uint8_t> &payload, std::uint16_t &code,
				    std::uint64_t &ack_seq, std::string &msg)
{
	if (payload.size() < 14u) {
		return false;
	}
	code = read_u16(payload, 0u);
	ack_seq = read_u64(payload, 2u);
	const auto msg_len = read_u32(payload, 10u);
	if (payload.size() < 14u + msg_len) {
		return false;
	}
	msg.assign(reinterpret_cast<const char *>(payload.data() + 14u), msg_len);
	return true;
}

bool IpcBridge::parse_caption_payload(const std::vector<std::uint8_t> &payload, std::uint32_t &epoch,
				     std::uint64_t &ts, std::uint64_t &seq, bool &is_final,
				     std::string &text)
{
	(void)is_final;
	if (payload.size() < 24u) {
		return false;
	}
	epoch = read_u32(payload, 0u);
	ts = read_u64(payload, 4u);
	seq = read_u64(payload, 12u);
	const auto text_len = read_u32(payload, 20u);
	if (payload.size() < 24u + text_len) {
		return false;
	}
	text.assign(reinterpret_cast<const char *>(payload.data() + 24u), text_len);
	return true;
}

std::uint16_t IpcBridge::status_reason(std::uint16_t code) const
{
	switch (code) {
	case 0u:
		return static_cast<std::uint16_t>(WaitReason::Complete);
	case 1u:
	case 2u:
	case 3u:
	case 4u:
		return static_cast<std::uint16_t>(WaitReason::Timeout);
	case 5u:
		return static_cast<std::uint16_t>(WaitReason::Superseded);
	case 6u:
		return static_cast<std::uint16_t>(WaitReason::Cancelled);
	case 7u:
		return static_cast<std::uint16_t>(WaitReason::NoSession);
	default:
		return static_cast<std::uint16_t>(WaitReason::Timeout);
	}
}

void IpcBridge::build_control_payload(std::vector<std::uint8_t> &payload, std::uint16_t command, std::uint64_t seq,
				     const std::vector<std::uint8_t> &arg)
{
	payload.clear();
	append_u16(payload, command);
	append_u64(payload, seq);
	append_u32(payload, static_cast<std::uint32_t>(arg.size()));
	payload.insert(payload.end(), arg.begin(), arg.end());
}

void IpcBridge::build_audio_payload(std::vector<std::uint8_t> &payload, const AudioSlot &slot)
{
	payload.clear();
	const auto timestamp_ns = (slot.seq * 1000000000ull) / kSampleRate;
	append_u64(payload, timestamp_ns);
	append_u32(payload, static_cast<std::uint32_t>(slot.num_samples));

	for (std::size_t i = 0u; i < slot.num_samples; ++i) {
		const std::int32_t i16 = static_cast<std::int32_t>(std::llround(slot.samples[i] * 32767.0f));
		const auto sample = std::clamp<std::int32_t>(i16, -32768, 32767);
		payload.push_back(static_cast<std::uint8_t>(sample & 0xffu));
		payload.push_back(static_cast<std::uint8_t>((sample >> 8u) & 0xffu));
	}
}

std::uint64_t IpcBridge::now_ns() const
{
	return std::chrono::duration_cast<std::chrono::nanoseconds>(
		       std::chrono::steady_clock::now().time_since_epoch())
		.count();
}

} // namespace obs_native_ipc
