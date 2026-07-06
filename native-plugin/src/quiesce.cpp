// SPDX-License-Identifier: GPL-2.0
#include "quiesce.hpp"

#include <thread>

namespace obs_native_ipc {

bool ProducerQuiesce::producer_enter()
{
	const auto new_inflight = inflight_frames_.fetch_add(1, std::memory_order_seq_cst) + 1;
	(void)new_inflight;
	if (!accepting_audio_.load(std::memory_order_seq_cst)) {
		inflight_frames_.fetch_sub(1, std::memory_order_seq_cst);
		return false;
	}
	return true;
}

void ProducerQuiesce::producer_leave()
{
	inflight_frames_.fetch_sub(1, std::memory_order_seq_cst);
}

bool ProducerQuiesce::begin_quiesce_and_wait(std::chrono::milliseconds timeout)
{
	const auto deadline = std::chrono::steady_clock::now() + timeout;
	accepting_audio_.store(false, std::memory_order_seq_cst);

	while (inflight_frames_.load(std::memory_order_seq_cst) != 0) {
		if (std::chrono::steady_clock::now() >= deadline) {
			return false;
		}
		std::this_thread::yield();
	}
	return true;
}

} // namespace obs_native_ipc
