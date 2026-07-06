// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include "epoch_gate.hpp"
#include "framing.hpp"
#include "ipc-transport.hpp"
#include "out_queue.hpp"
#include "quiesce.hpp"
#include "ring.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace obs_native_ipc {

// Fixed-slot audio sample packet for SPSC bridge ring.
struct AudioSlot {
	std::uint64_t seq{0};
	std::uint64_t marker{0};
	bool is_consistent() const { return marker == ~seq; }
	static constexpr std::size_t kMaxSamples = 1920;
	std::array<float, kMaxSamples> samples{};
	std::size_t num_samples{0};
	std::size_t num_channels{1};
};

class IpcBridge {
public:
	struct Config {
		SpawnConfig spawn;
		std::string config_path;
		std::size_t ring_capacity{64};
		std::chrono::milliseconds hello_timeout{1000};
		std::chrono::milliseconds heartbeat_interval{250};
		std::chrono::milliseconds heartbeat_timeout{750};
		std::chrono::milliseconds restart_base_backoff{500};
		std::chrono::milliseconds restart_max_backoff{30000};
		std::chrono::milliseconds stop_timeout{1500};
	};

	using CaptionCallback = std::function<void(const CaptionEvent &)>;

	IpcBridge();
	~IpcBridge();

	bool start(const Config &cfg);
	void stop();
	std::uint32_t active_epoch() const;
	void set_caption_callback(CaptionCallback cb);
	void push_audio(const float *planar, std::size_t num_samples, std::size_t num_channels);

private:
	bool run_session_loop();
	bool start_session();
	void stop_session();
	void run_writer();
	void run_reader();
	void run_heartbeat();
	void request_restart();
	void join_threads(std::chrono::milliseconds timeout);

	bool send_hello(std::uint32_t session_epoch);
	bool wait_for_ready(std::uint32_t session_epoch);
	bool write_exact(const std::uint8_t *data, std::size_t size);
	void process_payload(const DecodedFrame &frame, std::uint32_t reader_epoch);
	void apply_status(std::uint16_t code, std::uint64_t seq);
	void apply_caption(std::uint32_t epoch, std::uint64_t ts, std::uint64_t seq, bool is_final,
			   const std::string &text);
	void apply_flush_done(std::uint64_t seq);
	void set_last_heartbeat_now();
	void clear_control_queue();
	void backoff_sleep(std::size_t restart_count);
	std::uint64_t now_ns() const;

	void append_u16(std::vector<std::uint8_t> &out, std::uint16_t value);
	void append_u32(std::vector<std::uint8_t> &out, std::uint32_t value);
	void append_u64(std::vector<std::uint8_t> &out, std::uint64_t value);
	bool parse_ready_payload(const std::vector<std::uint8_t> &payload, std::uint16_t &accepted_version,
			       std::uint32_t &epoch, bool &supports_partial);
	bool parse_status_payload(const std::vector<std::uint8_t> &payload, std::uint16_t &code,
				std::uint64_t &ack_seq, std::string &msg);
	bool parse_caption_payload(const std::vector<std::uint8_t> &payload, std::uint32_t &epoch,
				std::uint64_t &ts, std::uint64_t &seq, bool &is_final, std::string &text);
	std::uint16_t status_reason(std::uint16_t code) const;
	void build_control_payload(std::vector<std::uint8_t> &payload, std::uint16_t command,
				   std::uint64_t seq, const std::vector<std::uint8_t> &arg);
	void build_audio_payload(std::vector<std::uint8_t> &payload, const AudioSlot &slot);

	Config config_;
	ChildTransport transport_;
	OutQueue control_queue_{16};
	EpochGate epoch_gate_{0};
	ProducerQuiesce quiesce_;
	SeqlockRing<AudioSlot> audio_ring_{64};

	std::atomic<bool> running_{false};
	std::atomic<bool> stop_requested_{false};
	std::atomic<bool> restart_requested_{false};
	std::atomic<bool> session_ready_{false};
	std::atomic<bool> session_running_{false};
	std::atomic<std::uint32_t> session_epoch_{0};
	std::atomic<std::uint64_t> next_control_seq_{1u};
	std::atomic<std::uint64_t> next_audio_seq_{1u};

	std::thread supervisor_thread_;
	std::thread writer_thread_;
	std::thread reader_thread_;
	std::thread heartbeat_thread_;
	std::mutex transport_write_mutex_;
	mutable std::mutex state_mutex_;
	std::condition_variable ready_cv_;
	std::condition_variable state_cv_;

	CaptionCallback caption_callback_;
	std::mutex callback_mutex_;

	std::atomic<std::uint64_t> last_heartbeat_ns_{0u};
};

} // namespace obs_native_ipc
