// SPDX-License-Identifier: GPL-2.0
#include "quiesce.hpp"

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

	ProducerQuiesce barrier;
	assert_true(barrier.producer_enter(), "enter should succeed while accepting");
	assert_true(!barrier.begin_quiesce_and_wait(std::chrono::milliseconds{50}),
		    "quiesce should wait while producer in-flight");
	barrier.producer_leave();
	assert_true(barrier.begin_quiesce_and_wait(std::chrono::milliseconds{200}),
		    "quiesce should drain after producer leaves");

	ProducerQuiesce barrier2;
	std::atomic<bool> entered{false};
	std::atomic<bool> allow_leave{false};
	std::thread producer([&barrier2, &entered, &allow_leave]() {
		if (!barrier2.producer_enter()) {
			return;
		}
		entered.store(true, std::memory_order_release);
		while (!allow_leave.load(std::memory_order_acquire)) {
			std::this_thread::yield();
		}
		barrier2.producer_leave();
	});

	while (!entered.load(std::memory_order_acquire)) {
		std::this_thread::yield();
	}
	assert_true(!barrier2.begin_quiesce_and_wait(std::chrono::milliseconds{50}),
		    "count-then-check race should wait until leaving producer");
	allow_leave.store(true, std::memory_order_release);
	producer.join();
	assert_true(barrier2.begin_quiesce_and_wait(std::chrono::milliseconds{200}),
		    "barrier2 should drain after producer allowed to leave");

	std::cout << "quiesce_test: PASS" << std::endl;
	return 0;
}
