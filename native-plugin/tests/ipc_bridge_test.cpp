// SPDX-License-Identifier: GPL-2.0

#include "ipc_bridge_test_helpers.hpp"

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <thread>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

using namespace std::chrono_literals;

void test_hello_roundtrip(const char *argv0)
{
	using namespace obs_native_ipc;
	IpcBridge bridge;
	std::atomic<int> seen{0};
	bridge.set_caption_callback([&](const CaptionEvent &) { seen.fetch_add(1, std::memory_order_relaxed); });

	IpcBridge::Config cfg;
	cfg.spawn = fake_config(argv0, "basic");
	cfg.config_path = "test-config";
	cfg.hello_timeout = 1200ms;
	cfg.heartbeat_interval = 100ms;
	cfg.heartbeat_timeout = 700ms;

	assert_true(bridge.start(cfg), "hello round-trip should reach ready");
	assert_true(bridge.active_epoch() == 1u, "epoch should be 1 after first start");
	assert_true(seen.load() == 0, "basic fake child should not emit captions");
	bridge.stop();
}

void test_restart_and_stale_epoch(const char *argv0)
{
	using namespace obs_native_ipc;
	IpcBridge bridge;
	std::atomic<int> seen{0};
	bridge.set_caption_callback([&](const CaptionEvent &) {
		seen.fetch_add(1, std::memory_order_relaxed);
	});

	IpcBridge::Config cfg;
	cfg.spawn = fake_config(argv0, "heartbeat_restart");
	cfg.config_path = "test-config";
	cfg.hello_timeout = 1200ms;
	cfg.heartbeat_interval = 25ms;
	cfg.heartbeat_timeout = 120ms;
	cfg.restart_base_backoff = 20ms;
	cfg.restart_max_backoff = 200ms;

	const auto start = std::chrono::steady_clock::now();
	assert_true(bridge.start(cfg), "bridge should start with heartbeat-restart fake child");
	const std::uint32_t first_epoch = bridge.active_epoch();
	assert_true(first_epoch == 1u, "first session epoch should be 1");

	const auto deadline = start + 4000ms;
	bool restarted = false;
	while (std::chrono::steady_clock::now() < deadline) {
		if (bridge.active_epoch() > first_epoch) {
			restarted = true;
			break;
		}
		std::this_thread::sleep_for(20ms);
	}
	assert_true(restarted, "bridge should restart after heartbeat loss");
	std::this_thread::sleep_for(200ms);
	assert_true(seen.load() == 0, "stale-caption before callback should be ignored by epoch gate");
	bridge.stop();
}

void test_stop_finite_on_wedge_child(const char *argv0)
{
	using namespace obs_native_ipc;
	IpcBridge bridge;
	IpcBridge::Config cfg;
	cfg.spawn = fake_config(argv0, "wedge");
	cfg.config_path = "test-config";
	cfg.hello_timeout = 1200ms;
	cfg.heartbeat_interval = 100ms;
	cfg.heartbeat_timeout = 5s;
	cfg.stop_timeout = 1500ms;

	assert_true(bridge.start(cfg), "wedge child should start");
	const auto start = std::chrono::steady_clock::now();
	bridge.stop();
	const auto elapsed = std::chrono::steady_clock::now() - start;
	assert_true(elapsed < 1500ms, "stop should return within finite time for wedged child");
}

void test_start_failure_cleanly_destroys_bridge(const char *argv0)
{
	using namespace obs_native_ipc;
	IpcBridge bridge;
	IpcBridge::Config cfg;
	cfg.spawn = fake_config(argv0, "exit_before_ready");
	cfg.config_path = "test-config";
	cfg.hello_timeout = 200ms;
	cfg.restart_base_backoff = 1ms;
	cfg.restart_max_backoff = 2ms;
	cfg.stop_timeout = 500ms;

	assert_true(!bridge.start(cfg), "child that exits before READY should fail start");
	bridge.stop();
}

int main(int argc, char **argv)
{
	if (argc == 3 && std::strcmp(argv[1], "--fake-child") == 0) {
#ifdef _WIN32
		_setmode(_fileno(stdin), _O_BINARY);
		_setmode(_fileno(stdout), _O_BINARY);
#endif
		if (std::getenv("OBS_BRIDGE_TEST_CHILD_LOG") != nullptr) {
			child_log("fake_child branch_started");
			child_log(argv[2]);
		}
		return run_fake_child(argv[2]);
	}

	assert_true(argc >= 1, "argv0 exists");
	test_hello_roundtrip(argv[0]);
	test_restart_and_stale_epoch(argv[0]);
	test_stop_finite_on_wedge_child(argv[0]);
	test_start_failure_cleanly_destroys_bridge(argv[0]);

	std::cout << "ipc_bridge_test: PASS" << std::endl;
	return 0;
}
