// SPDX-License-Identifier: GPL-2.0
#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <utility>
#include <vector>

namespace obs_native_ipc {

struct RingFrame {
	std::uint64_t seq{0};
	std::uint64_t marker{0};

	bool is_consistent() const
	{
		return marker == ~seq;
	}
};

class SeqlockRing {
public:
	using value_type = RingFrame;

	struct PushResult {
		bool dropped_oldest{false};
	};

	explicit SeqlockRing(std::size_t capacity);

	PushResult push(const value_type &value);
	bool pop(value_type &value);
	std::size_t dropped_frames() const noexcept;
	std::size_t capacity() const noexcept;

private:
	struct Slot {
		std::atomic<std::uint32_t> version{0};
		value_type value{};
	};

	std::vector<Slot> buffer_;
	const std::size_t capacity_;
	std::atomic<std::size_t> head_{0};
	std::atomic<std::size_t> tail_{0};
	std::atomic<std::size_t> dropped_frames_{0};
	std::uint64_t last_popped_seq_{0};
	bool has_last_popped_seq_{false};
};

} // namespace obs_native_ipc
