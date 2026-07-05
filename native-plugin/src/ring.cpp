// SPDX-License-Identifier: GPL-2.0
#include "ring.hpp"

namespace obs_native_ipc {

SeqlockRing::SeqlockRing(std::size_t capacity) : buffer_(capacity ? capacity : 1), capacity_(capacity ? capacity : 1)
{
	if (capacity == 0) {
		throw std::invalid_argument("ring capacity must be non-zero");
	}
}

SeqlockRing::PushResult SeqlockRing::push(const value_type &value)
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

bool SeqlockRing::pop(value_type &value)
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

std::size_t SeqlockRing::dropped_frames() const noexcept
{
	return dropped_frames_.load(std::memory_order_acquire);
}

std::size_t SeqlockRing::capacity() const noexcept
{
	return capacity_;
}

} // namespace obs_native_ipc
