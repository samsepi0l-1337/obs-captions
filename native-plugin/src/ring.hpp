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

template <typename T>
class SeqlockRing {
public:
	using value_type = T;

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

template <typename T>
SeqlockRing<T>::SeqlockRing(std::size_t capacity) : buffer_(capacity ? capacity : 1), capacity_(capacity ? capacity : 1)
{
	if (capacity == 0) {
		throw std::invalid_argument("ring capacity must be non-zero");
	}
}

template <typename T>
typename SeqlockRing<T>::PushResult SeqlockRing<T>::push(const value_type &value)
{
	PushResult result;

	const std::size_t tail = tail_.load(std::memory_order_relaxed);
	auto &slot = buffer_[tail % capacity_];

	slot.version.fetch_add(1, std::memory_order_acq_rel);
	slot.value = value;
	slot.version.fetch_add(1, std::memory_order_release);

	const std::size_t next_tail = tail + 1u;
	tail_.store(next_tail, std::memory_order_release);

	const std::size_t head = head_.load(std::memory_order_acquire);
	if (next_tail > head + capacity_) {
		dropped_frames_.fetch_add(1u, std::memory_order_acq_rel);
		result.dropped_oldest = true;
	}

	return result;
}

template <typename T>
bool SeqlockRing<T>::pop(value_type &value)
{
	while (true) {
		const std::size_t head = head_.load(std::memory_order_acquire);
		const std::size_t tail = tail_.load(std::memory_order_acquire);
		if (head >= tail) {
			return false;
		}

		const std::size_t distance = tail - head;
		if (distance > capacity_) {
			const std::size_t catch_up = distance - capacity_;
			head_.fetch_add(catch_up, std::memory_order_release);
			continue;
		}

		Slot &slot = buffer_[head % capacity_];
		const std::uint32_t before = slot.version.load(std::memory_order_acquire);
		if ((before & 1u) != 0u) {
			head_.fetch_add(1u, std::memory_order_release);
			dropped_frames_.fetch_add(1u, std::memory_order_acq_rel);
			continue;
		}

		const value_type copied = slot.value;
		const std::uint32_t after = slot.version.load(std::memory_order_acquire);
		if (before != after) {
			head_.fetch_add(1u, std::memory_order_release);
			dropped_frames_.fetch_add(1u, std::memory_order_acq_rel);
			continue;
		}
		if (!copied.is_consistent()) {
			head_.fetch_add(1u, std::memory_order_release);
			dropped_frames_.fetch_add(1u, std::memory_order_acq_rel);
			continue;
		}

		if (has_last_popped_seq_ && copied.seq <= last_popped_seq_) {
			head_.fetch_add(1u, std::memory_order_release);
			dropped_frames_.fetch_add(1u, std::memory_order_acq_rel);
			continue;
		}

		value = copied;
		last_popped_seq_ = copied.seq;
		has_last_popped_seq_ = true;
		head_.fetch_add(1u, std::memory_order_release);
		return true;
	}
}

template <typename T>
std::size_t SeqlockRing<T>::dropped_frames() const noexcept
{
	return dropped_frames_.load(std::memory_order_acquire);
}

template <typename T>
std::size_t SeqlockRing<T>::capacity() const noexcept
{
	return capacity_;
}

using RingFrameRing = SeqlockRing<RingFrame>;

} // namespace obs_native_ipc
