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
	(void)timeout;
	if (writer_thread_.joinable()) {
		writer_thread_.join();
	}
	if (reader_thread_.joinable()) {
		reader_thread_.join();
	}
	if (heartbeat_thread_.joinable()) {
		heartbeat_thread_.join();
	}
}

void IpcBridge::clear_control_queue()
{
	control_queue_.~OutQueue();
	new (&control_queue_) OutQueue();
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
	std::this_thread::sleep_for(delay);
}

} // namespace obs_native_ipc
