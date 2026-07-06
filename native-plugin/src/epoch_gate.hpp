// SPDX-License-Identifier: GPL-2.0
#pragma once

#include <atomic>
#include <cstdint>
#include <string>

namespace obs_native_ipc {

enum class CaptionDecision {
	Accept,
	DropStaleEpoch,
	DropOutOfOrder,
	DropDuplicate,
};

struct CaptionEvent {
	std::uint32_t epoch{0};
	std::uint64_t seq{0};
	std::uint64_t timestamp_ns{0};
	std::string text;
	bool is_final{false};
};

class EpochGate {
public:
	explicit EpochGate(std::uint32_t initial_epoch = 0);

	std::uint32_t active_epoch() const;
	void advance_epoch();
	bool should_apply(std::uint32_t reader_epoch) const;
	CaptionDecision evaluate(const CaptionEvent &event);

private:
	// The epoch value is shared across threads; keep it atomic.
	std::atomic<std::uint32_t> active_epoch_{0};
	// The following dedupe state is reader-thread owned.
	bool has_last_{false};
	std::uint64_t last_seq_{0};
	std::uint64_t last_timestamp_{0};
	std::string last_text_;
};

} // namespace obs_native_ipc
