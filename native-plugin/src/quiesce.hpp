// SPDX-License-Identifier: GPL-2.0
#pragma once

#include <atomic>
#include <chrono>

namespace obs_native_ipc {

class ProducerQuiesce {
public:
	bool producer_enter();
	void producer_leave();
	bool begin_quiesce_and_wait(std::chrono::milliseconds timeout);

private:
	std::atomic<bool> accepting_audio_{true};
	std::atomic<std::uint32_t> inflight_frames_{0};
};

} // namespace obs_native_ipc
