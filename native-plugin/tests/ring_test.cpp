// SPDX-License-Identifier: GPL-2.0
#include "ring.hpp"

#include <cstdlib>
#include <atomic>
#include <chrono>
#include <iostream>
#include <thread>

void assert_true(bool cond, const char *message)
{
	if (!cond) {
		std::cerr << "FAILED: " << message << std::endl;
		std::exit(1);
	}
}

int main()
{
	using namespace obs_native_ipc;

	SeqlockRing ring(3);
	SeqlockRing::PushResult result;

	result = ring.push({1u, ~std::uint64_t{1u}});
	assert_true(!result.dropped_oldest, "first push shouldn't drop");
	result = ring.push({2u, ~std::uint64_t{2u}});
	assert_true(!result.dropped_oldest, "second push shouldn't drop");
	result = ring.push({3u, ~std::uint64_t{3u}});
	assert_true(!result.dropped_oldest, "third push shouldn't drop");
	result = ring.push({4u, ~std::uint64_t{4u}});
	assert_true(result.dropped_oldest, "fourth push should drop oldest");
	assert_true(ring.dropped_frames() == 1u, "dropped counter should be 1");

	RingFrame frame{};
	assert_true(ring.pop(frame), "pop should succeed after one overflow");
	assert_true(frame.seq == 2u, "first popped should be seq2");
	assert_true(ring.pop(frame), "pop should succeed");
	assert_true(frame.seq == 3u, "second popped should be seq3");
	assert_true(ring.pop(frame), "pop should succeed");
	assert_true(frame.seq == 4u, "third popped should be seq4");
	assert_true(!ring.pop(frame), "ring should empty");

	const std::size_t capacity = 2048u;
	SeqlockRing stress_ring(capacity);
	std::atomic<bool> producer_done{false};
	std::atomic<bool> okay{true};
	std::atomic<int> pushed_frames{0};
	std::atomic<int> popped_frames{0};

	std::thread producer([&stress_ring, &producer_done, &okay, &pushed_frames]() {
		for (std::uint64_t i = 1u; i < 10000u; ++i) {
			if (!okay.load(std::memory_order_relaxed)) {
				return;
			}
			stress_ring.push({i, ~i});
			pushed_frames.fetch_add(1, std::memory_order_relaxed);
		}
		producer_done.store(true, std::memory_order_release);
	});

	std::thread consumer([&stress_ring, &producer_done, &okay, &pushed_frames, &popped_frames]() {
		RingFrame value{};
		std::uint64_t last_seen = 0u;
		bool has_last_seen = false;
		int spin_without_pop = 0;
		while (!producer_done.load(std::memory_order_acquire) || popped_frames.load(std::memory_order_acquire) < pushed_frames.load(std::memory_order_acquire)) {
			if (stress_ring.pop(value)) {
				popped_frames.fetch_add(1, std::memory_order_relaxed);
				spin_without_pop = 0;
				if (value.seq == 0u || !value.is_consistent() || (has_last_seen && value.seq <= last_seen)) {
					okay.store(false, std::memory_order_relaxed);
					return;
				}
				if (value.seq != 0u) {
					last_seen = value.seq;
					has_last_seen = true;
				}
			} else {
				std::this_thread::yield();
				if (++spin_without_pop > 20000) {
					break;
				}
			}
		}
	});

	producer.join();
	consumer.join();
	assert_true(okay.load(), "torn read should not corrupt ring values");
	assert_true(popped_frames.load() <= pushed_frames.load(), "popped should not exceed pushed");
	assert_true(popped_frames.load() >= 1, "some frames should be consumed");

	for (std::size_t capacity = 2u; capacity <= 6u; ++capacity) {
		std::atomic<bool> any_drop{false};
		for (std::size_t run = 0u; run < 128u; ++run) {
			SeqlockRing tiny_ring(capacity);
			std::atomic<bool> producer_done{false};
			std::atomic<bool> okay{true};
			std::atomic<bool> popped_once{false};

			std::thread producer([&tiny_ring, &producer_done, &any_drop]() {
				for (std::uint64_t i = 1u; i <= 6000u; ++i) {
					const auto result = tiny_ring.push({i, ~i});
					if (result.dropped_oldest) {
						any_drop.store(true, std::memory_order_relaxed);
					}
					if ((i & 3u) == 0u) {
						std::this_thread::yield();
					}
				}
				producer_done.store(true, std::memory_order_release);
			});

			std::thread consumer([&tiny_ring, &producer_done, &okay, &popped_once]() {
				RingFrame value{};
				std::uint64_t last_seen = 0u;
				bool has_last_seen = false;
				int spin_without_pop = 0;

				while (true) {
					if (tiny_ring.pop(value)) {
						popped_once.store(true, std::memory_order_relaxed);
						spin_without_pop = 0;
						if (!value.is_consistent()) {
							okay.store(false, std::memory_order_relaxed);
							return;
						}
						if (has_last_seen && value.seq <= last_seen) {
							okay.store(false, std::memory_order_relaxed);
							return;
						}
						last_seen = value.seq;
						has_last_seen = true;
						if ((value.seq & 0x3u) == 0u) {
							std::this_thread::yield();
						}
						continue;
					}

					if (producer_done.load(std::memory_order_acquire) && spin_without_pop > 20000) {
						break;
					}
					++spin_without_pop;
					if ((spin_without_pop & 3) == 0) {
						std::this_thread::yield();
					}
				}
			});

			producer.join();
			consumer.join();

			assert_true(producer_done.load(std::memory_order_acquire), "producer should finish tiny capacity stress");
			assert_true(popped_once.load(), "tiny capacity stress should consume some frames");
			assert_true(okay.load(), "tiny capacity stress should not regress sequence order");
		}
		assert_true(any_drop.load(), "tiny capacity stress should trigger at least one drop-oldest");
	}

	std::cout << "ring_test: PASS" << std::endl;
	return 0;
}
