// SPDX-License-Identifier: GPL-2.0-or-later
#include "ipc-bridge.hpp"

#include <algorithm>
#include <chrono>
#include <new>
#include <thread>

namespace obs_native_ipc {

IpcBridge::IpcBridge() = default;

IpcBridge::~IpcBridge()
{
	stop();
}

void IpcBridge::join_threads(std::chrono::milliseconds timeout)
{
	const auto deadline = std::chrono::steady_clock::now() + timeout;
	auto wait_done = [&](const std::atomic<bool> &done) {
		std::unique_lock lock(state_mutex_);
		return state_cv_.wait_until(lock, deadline, [&]() { return done.load(std::memory_order_acquire); });
	};
	auto join_or_degrade = [&](std::thread &thread, const std::atomic<bool> &done) {
		if (!thread.joinable()) {
			return;
		}
		if (!wait_done(done)) {
			mark_degraded();
			thread.detach();
			return;
		}
		thread.join();
	};

	join_or_degrade(writer_thread_, writer_exited_);
	join_or_degrade(reader_thread_, reader_exited_);
	join_or_degrade(heartbeat_thread_, heartbeat_exited_);
}

void IpcBridge::clear_control_queue()
{
#ifdef OBS_NATIVE_IPC_TESTING
	test_control_queue_reset_count_.fetch_add(1u, std::memory_order_acq_rel);
#endif
	control_queue_.~OutQueue();
	new (&control_queue_) OutQueue(16u);
}

void IpcBridge::backoff_sleep(std::size_t restart_count)
{
	std::size_t scale = 1u << std::min<std::size_t>(restart_count, 6u);
	auto delay = std::chrono::duration_cast<std::chrono::milliseconds>(config_.restart_base_backoff) *
		     static_cast<int>(scale);
	if (delay > config_.restart_max_backoff) {
		delay = config_.restart_max_backoff;
	}
	const auto jitter_ns = session_epoch_.load(std::memory_order_acquire) % 11ull;
	delay += std::chrono::milliseconds(static_cast<long long>(jitter_ns));

	std::unique_lock lock(state_mutex_);
	state_cv_.wait_for(lock, delay, [this]() { return desired() == Desired::Destroy; });
}

} // namespace obs_native_ipc
