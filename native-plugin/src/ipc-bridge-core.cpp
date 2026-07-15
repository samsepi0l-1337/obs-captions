// SPDX-License-Identifier: GPL-2.0-or-later
#include "ipc-bridge.hpp"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <new>
#include <thread>
#include <vector>

namespace obs_native_ipc {

namespace {
class ScopeExit {
public:
	explicit ScopeExit(std::function<void()> fn) : fn_(std::move(fn)) {}
	~ScopeExit()
	{
		if (fn_) {
			fn_();
		}
	}

private:
	std::function<void()> fn_;
};
} // namespace

bool IpcBridge::start(const Config &cfg)
{
	if (running_.load(std::memory_order_acquire) || cfg.spawn.argv.empty()) {
		return false;
	}

	config_ = cfg;
	if (config_.ring_capacity == 0u) {
		config_.ring_capacity = 64u;
	}

	reset_audio_path(config_.ring_capacity);
	clear_control_queue();
	{
		std::lock_guard lock(epoch_mutex_);
		epoch_gate_.~EpochGate();
		new (&epoch_gate_) EpochGate(0);
	}

	desired_.store(static_cast<std::uint8_t>(Desired::Run), std::memory_order_release);
	teardown_owner_.store(false, std::memory_order_release);
	no_respawn_.store(false, std::memory_order_release);
	terminal_state_.store(static_cast<std::uint8_t>(TerminalState::None), std::memory_order_release);
	ring_released_.store(false, std::memory_order_release);
	running_.store(true, std::memory_order_release);
	stop_requested_.store(false, std::memory_order_release);
	restart_requested_.store(false, std::memory_order_release);
	set_ready_state(false);
	session_running_.store(false, std::memory_order_release);
	next_audio_seq_.store(1u, std::memory_order_relaxed);
	next_control_seq_.store(1u, std::memory_order_relaxed);

	supervisor_thread_ = std::thread(&IpcBridge::run_session_loop, this);

	bool ready = false;
	{
		std::unique_lock lock(state_mutex_);
		ready = ready_cv_.wait_for(lock, cfg.hello_timeout, [this]() {
			return desired() != Desired::Run || session_ready_.load(std::memory_order_acquire) ||
			       !running_.load(std::memory_order_acquire);
		});
	}
	if (!ready || desired() != Desired::Run || !session_ready_.load(std::memory_order_acquire) ||
	    !session_running_.load(std::memory_order_acquire) || !running_.load(std::memory_order_acquire)) {
		stop();
		return false;
	}
	return true;
}

void IpcBridge::stop()
{
	request_destroy();
	if (supervisor_thread_.joinable()) {
		supervisor_thread_.join();
	}
	join_threads(config_.stop_timeout);
}

std::uint32_t IpcBridge::active_epoch() const
{
	std::lock_guard lock(epoch_mutex_);
	return epoch_gate_.active_epoch();
}

void IpcBridge::set_caption_callback(CaptionCallback cb)
{
	std::lock_guard lock(callback_mutex_);
	caption_callback_ = std::move(cb);
}

void IpcBridge::push_audio(const float *planar, std::size_t num_samples, std::size_t num_channels)
{
	auto quiesce = quiesce_snapshot();
	if (!quiesce || !quiesce->producer_enter()) {
		return;
	}
	ScopeExit leave([&]() { quiesce->producer_leave(); });

#ifdef OBS_NATIVE_IPC_TESTING
	if (test_pause_after_producer_enter_.load(std::memory_order_acquire)) {
		{
			std::lock_guard lock(state_mutex_);
			test_paused_after_producer_enter_.store(true, std::memory_order_release);
		}
		test_cv_.notify_all();
		std::unique_lock lock(state_mutex_);
		test_cv_.wait(lock, [this]() { return test_release_after_producer_enter_.load(std::memory_order_acquire); });
	}
#endif

	if (!running_.load(std::memory_order_acquire) || !session_running_.load(std::memory_order_acquire) || !planar ||
	    num_samples == 0u || num_channels == 0u) {
		return;
	}

	auto ring = audio_ring_snapshot();
	if (!ring) {
		return;
	}

	const std::size_t total_frames = num_channels == 1u ? num_samples : (num_samples / num_channels);
	std::size_t offset = 0u;
	while (offset < total_frames) {
		AudioSlot slot;
		slot.num_channels = num_channels;
		slot.num_samples = std::min<std::size_t>(AudioSlot::kMaxSamples, total_frames - offset);
		slot.seq = next_audio_seq_.fetch_add(slot.num_samples, std::memory_order_relaxed);
		slot.marker = ~slot.seq;

		for (std::size_t i = 0u; i < slot.num_samples; ++i) {
			float value = 0.0f;
			if (num_channels == 1u) {
				value = planar[offset + i];
			} else {
				const std::size_t frame = offset + i;
				for (std::size_t ch = 0u; ch < num_channels; ++ch) {
					value += planar[(frame * num_channels) + ch];
				}
				value = value / static_cast<float>(num_channels);
			}
			slot.samples[i] = std::clamp(value, -1.0f, 1.0f);
		}

		ring->push(slot);
		offset += slot.num_samples;
	}
}

bool IpcBridge::run_session_loop()
{
	std::size_t restart_count = 0u;
	while (desired() != Desired::Destroy && !no_respawn_.load(std::memory_order_acquire)) {
		if (desired() == Desired::Run && !start_session()) {
			if (desired() == Desired::Run) {
				latch_desired(Desired::Restart);
			}
		}

		if (desired() == Desired::Run) {
			restart_count = 0u;
			std::unique_lock lock(state_mutex_);
			state_cv_.wait(lock, [this]() { return desired() != Desired::Run; });
		}

		const auto final_desired = execute_teardown();
		if (final_desired == Desired::Destroy || no_respawn_.load(std::memory_order_acquire)) {
			break;
		}
		++restart_count;
		backoff_sleep(restart_count);
	}
	set_ready_state(false);
	session_running_.store(false, std::memory_order_release);
	running_.store(false, std::memory_order_release);
	transport_.cancel();
	join_threads(config_.stop_timeout);
	state_cv_.notify_all();
	ready_cv_.notify_all();
	return true;
}

bool IpcBridge::start_session()
{
	const bool debug = std::getenv("OBS_BRIDGE_TEST_DEBUG") != nullptr;
	if (desired() != Desired::Run || no_respawn_.load(std::memory_order_acquire)) {
		return false;
	}

	session_running_.store(false, std::memory_order_release);
	set_ready_state(false);
	if (active_epoch() == 0u) {
		std::lock_guard lock(epoch_mutex_);
		epoch_gate_.advance_epoch();
	}
	const auto session_epoch = active_epoch();
	session_epoch_.store(session_epoch, std::memory_order_release);
	set_last_heartbeat_now();

	if (!transport_.spawn(config_.spawn)) {
		if (debug) {
			std::cerr << "spawn failed\n";
		}
		return false;
	}

	reader_exited_.store(false, std::memory_order_release);
	reader_thread_ = std::thread(&IpcBridge::run_reader, this);
	session_running_.store(true, std::memory_order_release);

	if (!send_hello(session_epoch) || !wait_for_ready(session_epoch)) {
		request_restart();
		return false;
	}

	const auto start_seq = next_control_seq_.fetch_add(1u, std::memory_order_relaxed);
	control_queue_.enqueue_control(ControlCommand::Start, start_seq, {});
	writer_exited_.store(false, std::memory_order_release);
	heartbeat_exited_.store(false, std::memory_order_release);
	writer_thread_ = std::thread(&IpcBridge::run_writer, this);
	heartbeat_thread_ = std::thread(&IpcBridge::run_heartbeat, this);
	ready_cv_.notify_all();
	return true;
}

void IpcBridge::run_writer()
{
	ScopeExit done([this]() {
		writer_exited_.store(true, std::memory_order_release);
		notify_thread_exit();
	});
#ifdef OBS_NATIVE_IPC_TESTING
	ScopeExit pause_before_exit([this]() {
		if (test_pause_writer_before_exit_.load(std::memory_order_acquire)) {
			{
				std::lock_guard lock(state_mutex_);
				test_paused_writer_before_exit_.store(true, std::memory_order_release);
			}
			test_cv_.notify_all();
			std::unique_lock lock(state_mutex_);
			test_cv_.wait(lock, [this]() { return test_release_writer_before_exit_.load(std::memory_order_acquire); });
		}
	});
#endif
	std::vector<std::uint8_t> payload;
	std::vector<std::uint8_t> frame_bytes;

	while (running_.load(std::memory_order_acquire) && desired() == Desired::Run &&
	       session_running_.load(std::memory_order_acquire)) {
		OutFrame frame;
		payload.clear();

		if (control_queue_.pop_next(frame)) {
			if (frame.kind == FrameKind::Control) {
				build_control_payload(payload, static_cast<std::uint16_t>(frame.control), frame.seq, frame.payload);
				frame_bytes = encode_frame(MessageType::CONTROL, payload);
			} else {
				frame_bytes = encode_frame(MessageType::HEARTBEAT, frame.payload);
			}
		} else {
			AudioSlot slot{};
			auto ring = audio_ring_snapshot();
			if (!ring || !ring->pop(slot)) {
				std::this_thread::sleep_for(std::chrono::milliseconds(1));
				continue;
			}
			build_audio_payload(payload, slot);
			frame_bytes = encode_frame(MessageType::AUDIO, payload);
		}

		if (!write_exact(frame_bytes.data(), frame_bytes.size())) {
			request_restart();
			return;
		}
	}
}

void IpcBridge::run_reader()
{
	ScopeExit done([this]() {
		reader_exited_.store(true, std::memory_order_release);
		notify_thread_exit();
	});
	std::vector<std::uint8_t> in_buf;
	in_buf.reserve(4096u);
	std::vector<std::uint8_t> read_buf(4096u);
	const auto reader_epoch = session_epoch_.load(std::memory_order_acquire);

	while (running_.load(std::memory_order_acquire) && desired() == Desired::Run &&
	       session_running_.load(std::memory_order_acquire)) {
		const auto n = transport_.read_some(read_buf.data(), read_buf.size());
		if (n <= 0) {
			request_restart();
			return;
		}
		in_buf.insert(in_buf.end(), read_buf.data(), read_buf.data() + static_cast<std::size_t>(n));

		while (true) {
			const auto decoded = try_decode_frame(in_buf);
			if (decoded.status == DecodeStatus::NeedMoreData) {
				break;
			}
			if (decoded.status != DecodeStatus::Ok || decoded.bytes_consumed == 0u ||
			    decoded.bytes_consumed > in_buf.size()) {
				request_restart();
				return;
			}
			in_buf.erase(in_buf.begin(), in_buf.begin() + decoded.bytes_consumed);
			process_payload(decoded.frame, reader_epoch);
		}
	}
}

void IpcBridge::run_heartbeat()
{
	ScopeExit done([this]() {
		heartbeat_exited_.store(true, std::memory_order_release);
		notify_thread_exit();
	});
	while (running_.load(std::memory_order_acquire) && desired() == Desired::Run &&
	       session_running_.load(std::memory_order_acquire)) {
		control_queue_.enqueue_heartbeat({});
		std::this_thread::sleep_for(config_.heartbeat_interval);

		const auto now = now_ns();
		const auto last = last_heartbeat_ns_.load(std::memory_order_acquire);
		const auto timeout_ns = static_cast<std::uint64_t>(config_.heartbeat_timeout.count()) * 1000000ull;
		if ((last != 0u) && (now >= last) && ((now - last) > timeout_ns)) {
			request_restart();
			return;
		}
	}
}

void IpcBridge::request_restart()
{
	if (!running_.load(std::memory_order_acquire) || no_respawn_.load(std::memory_order_acquire) ||
	    desired() == Desired::Destroy) {
		return;
	}
	latch_desired(Desired::Restart);
	restart_requested_.store(true, std::memory_order_release);
	state_cv_.notify_all();
	ready_cv_.notify_all();
}

} // namespace obs_native_ipc
