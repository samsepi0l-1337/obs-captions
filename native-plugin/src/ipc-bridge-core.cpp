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

bool IpcBridge::start(const Config &cfg)
{
	if (running_.load(std::memory_order_acquire)) {
		return false;
	}
	if (cfg.spawn.argv.empty()) {
		return false;
	}

	config_ = cfg;
	if (config_.ring_capacity == 0u) {
		config_.ring_capacity = 64u;
	}

	audio_ring_.~SeqlockRing<AudioSlot>();
	new (&audio_ring_) SeqlockRing<AudioSlot>(config_.ring_capacity);
	control_queue_.~OutQueue();
	new (&control_queue_) OutQueue(16u);

	running_.store(true, std::memory_order_release);
	stop_requested_.store(false, std::memory_order_release);
	restart_requested_.store(false, std::memory_order_release);
	session_ready_.store(false, std::memory_order_release);
	session_running_.store(false, std::memory_order_release);
	next_audio_seq_.store(1u, std::memory_order_relaxed);
	next_control_seq_.store(1u, std::memory_order_relaxed);
	quiesce_.begin_quiesce_and_wait(std::chrono::milliseconds(1));

	supervisor_thread_ = std::thread(&IpcBridge::run_session_loop, this);

	bool ready = false;
	{
		std::unique_lock lock(state_mutex_);
		ready = ready_cv_.wait_for(lock, cfg.hello_timeout, [this]() {
			return !running_.load(std::memory_order_acquire) ||
			       session_ready_.load(std::memory_order_acquire) ||
			       restart_requested_.load(std::memory_order_acquire);
		});
	}
	if (!ready || restart_requested_.load(std::memory_order_acquire) ||
	    !session_ready_.load(std::memory_order_acquire) ||
	    !session_running_.load(std::memory_order_acquire) ||
	    !running_.load(std::memory_order_acquire)) {
		stop();
		return false;
	}
	return true;
}

void IpcBridge::stop()
{
	running_.exchange(false, std::memory_order_acq_rel);
	stop_requested_.store(true, std::memory_order_release);
	restart_requested_.store(false, std::memory_order_release);
	transport_.cancel();
	state_cv_.notify_all();
	ready_cv_.notify_all();

	if (supervisor_thread_.joinable()) {
		supervisor_thread_.join();
	}
	join_threads(config_.stop_timeout);
}

std::uint32_t IpcBridge::active_epoch() const
{
	return epoch_gate_.active_epoch();
}

void IpcBridge::set_caption_callback(CaptionCallback cb)
{
	std::lock_guard lock(callback_mutex_);
	caption_callback_ = std::move(cb);
}

void IpcBridge::push_audio(const float *planar, std::size_t num_samples, std::size_t num_channels)
{
	if (!running_.load(std::memory_order_acquire) || !session_running_.load(std::memory_order_acquire) || !planar ||
	    num_samples == 0u || num_channels == 0u) {
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
					const std::size_t idx = (frame * num_channels) + ch;
					value += planar[idx];
				}
				value = value / static_cast<float>(num_channels);
			}
			slot.samples[i] = std::clamp(value, -1.0f, 1.0f);
		}

		audio_ring_.push(slot);
		offset += slot.num_samples;
	}
}

bool IpcBridge::run_session_loop()
{
	std::size_t restart_count = 0u;
	while (running_.load(std::memory_order_acquire) && !stop_requested_.load(std::memory_order_acquire)) {
		if (!start_session()) {
			stop_session();
			if (!running_.load(std::memory_order_acquire) || stop_requested_.load(std::memory_order_acquire)) {
				break;
			}
			++restart_count;
			backoff_sleep(restart_count);
			continue;
		}

		restart_count = 0u;
		{
			std::unique_lock lock(state_mutex_);
			state_cv_.wait(lock, [this]() {
				return stop_requested_.load(std::memory_order_acquire) ||
				       restart_requested_.load(std::memory_order_acquire) ||
				       !running_.load(std::memory_order_acquire);
			});
		}

		if (stop_requested_.load(std::memory_order_acquire) || !running_.load(std::memory_order_acquire)) {
			stop_session();
			break;
		}

		if (restart_requested_.exchange(false, std::memory_order_acq_rel)) {
			++restart_count;
			stop_session();
			backoff_sleep(restart_count);
		}
	}
	session_running_.store(false, std::memory_order_release);
	session_ready_.store(false, std::memory_order_release);
	transport_.cancel();
	join_threads(config_.stop_timeout);
	return true;
}

bool IpcBridge::start_session()
{
	const bool debug = std::getenv("OBS_BRIDGE_TEST_DEBUG") != nullptr;
	if (debug) {
		std::cerr << "start_session enter running=" << running_.load() << "\n";
	}
	stop_session();
	if (!running_.load(std::memory_order_acquire) || stop_requested_.load(std::memory_order_acquire)) {
		return false;
	}

	session_running_.store(false, std::memory_order_release);
	session_ready_.store(false, std::memory_order_release);
	next_audio_seq_.store(1u, std::memory_order_relaxed);
	audio_ring_.~SeqlockRing<AudioSlot>();
	new (&audio_ring_) SeqlockRing<AudioSlot>(config_.ring_capacity);
	control_queue_.~OutQueue();
	new (&control_queue_) OutQueue(16u);
	epoch_gate_.advance_epoch();

	const auto session_epoch = epoch_gate_.active_epoch();
	session_epoch_.store(session_epoch, std::memory_order_release);
	set_last_heartbeat_now();

	if (!transport_.spawn(config_.spawn)) {
		if (debug) {
			std::cerr << "spawn failed\n";
		}
		return false;
	}
	if (debug) {
		std::cerr << "spawned child alive=" << transport_.alive() << "\n";
	}

	reader_thread_ = std::thread(&IpcBridge::run_reader, this);
	session_running_.store(true, std::memory_order_release);

	if (!send_hello(session_epoch)) {
		if (debug) {
			std::cerr << "send_hello failed\n";
		}
		request_restart();
		return false;
	}
	if (debug) {
		std::cerr << "send_hello wrote, alive_immediate=" << transport_.alive() << "\n";
		std::this_thread::sleep_for(std::chrono::milliseconds(20));
		std::cerr << "send_hello wrote, alive_20ms=" << transport_.alive() << "\n";
	}
	if (!wait_for_ready(session_epoch)) {
		if (debug) {
			std::cerr << "wait_for_ready failed\n";
		}
		request_restart();
		return false;
	}

	session_ready_.store(true, std::memory_order_release);
	const auto start_seq = next_control_seq_.fetch_add(1u, std::memory_order_relaxed);
	control_queue_.enqueue_control(ControlCommand::Start, start_seq, {});
	writer_thread_ = std::thread(&IpcBridge::run_writer, this);
	heartbeat_thread_ = std::thread(&IpcBridge::run_heartbeat, this);
	ready_cv_.notify_all();
	if (debug) {
		std::cerr << "start_session success\n";
	}

	return true;
}

void IpcBridge::stop_session()
{
	const bool debug = std::getenv("OBS_BRIDGE_TEST_DEBUG") != nullptr;
	session_running_.store(false, std::memory_order_release);
	session_ready_.store(false, std::memory_order_release);
	ready_cv_.notify_all();
	restart_requested_.store(false, std::memory_order_release);

	transport_.cancel();
	join_threads(config_.stop_timeout);
	const auto exit_code = transport_.reap();
	if (debug) {
		std::cerr << "stop_session reap_code=" << exit_code << "\n";
	}

	quiesce_.begin_quiesce_and_wait(std::chrono::milliseconds(100));
	clear_control_queue();
}

void IpcBridge::run_writer()
{
	std::vector<std::uint8_t> payload;
	std::vector<std::uint8_t> frame_bytes;

	while (running_.load(std::memory_order_acquire) && !stop_requested_.load(std::memory_order_acquire) &&
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
			if (!audio_ring_.pop(slot)) {
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
	const bool debug = std::getenv("OBS_BRIDGE_TEST_DEBUG") != nullptr;
	if (debug) {
		std::cerr << "run_reader enter\n";
	}
	std::vector<std::uint8_t> in_buf;
	in_buf.reserve(4096u);
	std::vector<std::uint8_t> read_buf(4096u);
	const auto reader_epoch = session_epoch_.load(std::memory_order_acquire);
	const bool track_progress = debug;

	while (running_.load(std::memory_order_acquire) && !stop_requested_.load(std::memory_order_acquire) &&
	       session_running_.load(std::memory_order_acquire)) {
		if (track_progress) {
			std::cerr << "run_reader loop alive=" << transport_.alive() << "\n";
		}
		const auto n = transport_.read_some(read_buf.data(), read_buf.size());
		if (n <= 0) {
			if (debug) {
				std::cerr << "run_reader read_some n=" << n << " alive=" << transport_.alive() << "\n";
				if (!transport_.alive()) {
					std::cerr << "run_reader reap_on_dead=" << transport_.reap() << "\n";
				}
			}
			request_restart();
			return;
		}
		if (debug) {
			std::cerr << "run_reader got n=" << n << " epoch=" << reader_epoch << "\n";
		}
		in_buf.insert(in_buf.end(), read_buf.data(), read_buf.data() + static_cast<std::size_t>(n));

		while (true) {
			const auto decoded = try_decode_frame(in_buf);
			if (decoded.status == DecodeStatus::NeedMoreData) {
				if (debug) {
					std::cerr << "run_reader need_more_data in_size=" << in_buf.size() << "\n";
				}
				break;
			}
			if (decoded.status != DecodeStatus::Ok) {
				if (debug) {
					std::cerr << "run_reader decode_status=" << static_cast<int>(decoded.status)
						  << " size=" << in_buf.size() << "\n";
				}
				request_restart();
				return;
			}
			if (debug) {
				std::cerr << "run_reader decoded type=" << static_cast<int>(decoded.frame.type)
					  << " payload=" << decoded.frame.payload.size() << " consumed=" << decoded.bytes_consumed
					  << "\n";
			}
			if (decoded.bytes_consumed == 0u || decoded.bytes_consumed > in_buf.size()) {
				request_restart();
				return;
			}
			in_buf.erase(in_buf.begin(), in_buf.begin() + decoded.bytes_consumed);
			process_payload(decoded.frame, reader_epoch);
		}
	}
	if (debug) {
		std::cerr << "run_reader exit\n";
	}
}

void IpcBridge::run_heartbeat()
{
	while (running_.load(std::memory_order_acquire) && !stop_requested_.load(std::memory_order_acquire) &&
	       session_running_.load(std::memory_order_acquire)) {
		control_queue_.enqueue_heartbeat({});
		std::this_thread::sleep_for(config_.heartbeat_interval);

		const auto now = now_ns();
		const auto last = last_heartbeat_ns_.load(std::memory_order_acquire);
		const auto timeout_ns = static_cast<std::uint64_t>(config_.heartbeat_timeout.count()) * 1000000ull;
		if ((last == 0u) || (now < last)) {
			continue;
		}
		if ((now - last) > timeout_ns) {
			request_restart();
			return;
		}
	}
}

void IpcBridge::request_restart()
{
	if (!running_.load(std::memory_order_acquire) || stop_requested_.load(std::memory_order_acquire)) {
		return;
	}
	if (restart_requested_.exchange(true, std::memory_order_acq_rel)) {
		return;
	}
	state_cv_.notify_all();
}

} // namespace obs_native_ipc
