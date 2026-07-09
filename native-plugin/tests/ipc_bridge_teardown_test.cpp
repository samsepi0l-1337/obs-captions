// SPDX-License-Identifier: GPL-2.0

#include "ipc_bridge_test_helpers.hpp"

#include <atomic>
#include <chrono>
#include <cstring>
#include <iostream>
#include <new>
#include <thread>
#include <vector>

#ifndef __has_feature
#define __has_feature(x) 0
#endif

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

using namespace std::chrono_literals;
using obs_native_ipc::ControlCommand;
using obs_native_ipc::IpcBridge;
using obs_native_ipc::WaitReason;

namespace {
#if defined(__SANITIZE_THREAD__) || __has_feature(thread_sanitizer)
constexpr bool kThreadSanitizer = true;
#else
constexpr bool kThreadSanitizer = false;
#endif

constexpr auto kHelloTimeout = kThreadSanitizer ? 12000ms : 1200ms;
constexpr auto kShortWaitTimeout = kThreadSanitizer ? 5000ms : 1000ms;
constexpr auto kWaitTimeout = kThreadSanitizer ? 8000ms : 1500ms;
} // namespace

IpcBridge::Config cfg_for(const char *argv0, const char *mode)
{
	IpcBridge::Config cfg;
	cfg.spawn = fake_config(argv0, mode);
	cfg.config_path = "test-config";
	cfg.ring_capacity = 8;
	cfg.hello_timeout = kHelloTimeout;
	cfg.heartbeat_interval = 50ms;
	cfg.heartbeat_timeout = 2s;
	cfg.restart_base_backoff = 5ms;
	cfg.restart_max_backoff = 20ms;
	cfg.stop_timeout = 700ms;
	return cfg;
}

template <typename Fn>
bool wait_until(std::chrono::milliseconds timeout, Fn fn)
{
	const auto deadline = std::chrono::steady_clock::now() + timeout;
	while (std::chrono::steady_clock::now() < deadline) {
		if (fn()) {
			return true;
		}
		std::this_thread::sleep_for(5ms);
	}
	return fn();
}

void assert_bridge_started(IpcBridge &bridge, const IpcBridge::Config &cfg, const char *message)
{
#if defined(__SANITIZE_THREAD__) || __has_feature(thread_sanitizer)
	constexpr int kMaxTsanStartAttempts = 4;
	for (int attempt = 1; attempt < kMaxTsanStartAttempts; ++attempt) {
		if (bridge.start(cfg)) {
			return;
		}
		std::cerr << "TSan bridge start attempt " << attempt << '/' << kMaxTsanStartAttempts << " failed; retrying\n";
		bridge.~IpcBridge();
		new (&bridge) IpcBridge();
	}
	const bool started = bridge.start(cfg);
	if (!started) {
		std::cerr << "TSan bridge start attempt " << kMaxTsanStartAttempts << '/' << kMaxTsanStartAttempts
			  << " failed; no retries remain\n";
	}
	assert_true(started, message);
#else
	assert_true(bridge.start(cfg), message);
#endif
}

void start_bridge(IpcBridge &bridge, const char *argv0, const char *mode)
{
	auto cfg = cfg_for(argv0, mode);
	assert_bridge_started(bridge, cfg, "bridge should start");
}

void wait_in_flight(IpcBridge &bridge, ControlCommand cmd)
{
	assert_true(wait_until(kShortWaitTimeout, [&]() { return bridge.test_has_in_flight(cmd); }), "control should be in-flight");
}

void test_concurrent_duplicate_triggers(const char *argv0)
{
	IpcBridge bridge;
	start_bridge(bridge, argv0, "basic");
	const auto first_epoch = bridge.active_epoch();
	bridge.test_pause_before_respawn(true);
	std::vector<std::thread> threads;
	for (int i = 0; i < 12; ++i) {
		threads.emplace_back([&]() { bridge.test_request_restart(); });
	}
	for (auto &thread : threads) {
		thread.join();
	}
	assert_true(bridge.test_wait_before_respawn(kWaitTimeout), "restart should pause before respawn");
	assert_true(bridge.test_teardown_count() == 1u, "duplicate restart triggers should single-flight");
	bridge.test_release_before_respawn();
	assert_true(wait_until(kWaitTimeout, [&]() { return bridge.test_session_running() && bridge.active_epoch() > first_epoch; }),
		    "one restarted session should respawn");
	bridge.stop();
}

void test_restart_then_destroy(const char *argv0)
{
	IpcBridge bridge;
	start_bridge(bridge, argv0, "basic");
	bridge.test_pause_before_respawn(true);
	bridge.test_request_restart();
	assert_true(bridge.test_wait_before_respawn(kWaitTimeout), "restart teardown should reach respawn gate");
	std::atomic<bool> stopped{false};
	std::thread stopper([&]() {
		bridge.stop();
		stopped.store(true, std::memory_order_release);
	});
	std::this_thread::sleep_for(50ms);
	assert_true(!stopped.load(std::memory_order_acquire), "destroy should wait for owner gate");
	bridge.test_release_before_respawn();
	stopper.join();
	assert_true(bridge.test_terminal_state() == 1u, "destroy should win over restart and become INACTIVE");
	assert_true(!bridge.test_session_running(), "destroy should leave no running session");
}

void test_destroy_then_restart(const char *argv0)
{
	IpcBridge bridge;
	start_bridge(bridge, argv0, "basic");
	std::thread stopper([&]() { bridge.stop(); });
	std::this_thread::sleep_for(10ms);
	bridge.test_request_restart();
	stopper.join();
	assert_true(bridge.test_terminal_state() == 1u, "restart after destroy latch should be absorbed");
	assert_true(!bridge.test_session_running(), "destroy terminal should have no session");
}

void test_destroy_arriving_mid_clear_respawn(const char *argv0)
{
	test_restart_then_destroy(argv0);
}

void test_quiesce_before_ring_release(const char *argv0)
{
	IpcBridge bridge;
	start_bridge(bridge, argv0, "basic");
	bridge.test_pause_after_producer_enter(true);
	std::vector<float> audio(320, 0.1f);
	std::thread producer([&]() { bridge.push_audio(audio.data(), audio.size(), 1); });
	assert_true(bridge.test_wait_after_producer_enter(kShortWaitTimeout), "producer should pause after quiesce enter");
	std::atomic<bool> stopped{false};
	std::thread stopper([&]() {
		bridge.stop();
		stopped.store(true, std::memory_order_release);
	});
	std::this_thread::sleep_for(100ms);
	assert_true(!stopped.load(std::memory_order_acquire), "destroy should wait for producer drain");
	bridge.test_release_after_producer_enter();
	producer.join();
	stopper.join();
	assert_true(bridge.test_ring_released(), "ring should release after producer leaves");
}

void test_pending_waiter_cancel_on_destroy_and_restart(const char *argv0)
{
	IpcBridge bridge;
	start_bridge(bridge, argv0, "no_control_reply");
	auto destroy_waiter = bridge.test_enqueue_control(ControlCommand::Flush);
	wait_in_flight(bridge, ControlCommand::Flush);
	bridge.stop();
	assert_true(destroy_waiter->done(), "destroy should complete pending waiter");
	assert_true(destroy_waiter->reason() == WaitReason::Cancelled, "destroy waiter should be cancelled");

	IpcBridge restart_bridge;
	start_bridge(restart_bridge, argv0, "no_control_reply");
	auto restart_waiter = restart_bridge.test_enqueue_control(ControlCommand::Reconfigure);
	wait_in_flight(restart_bridge, ControlCommand::Reconfigure);
	restart_bridge.test_request_restart();
	assert_true(wait_until(kShortWaitTimeout, [&]() { return restart_waiter->done(); }), "restart should complete pending waiter");
	assert_true(restart_waiter->reason() == WaitReason::Cancelled, "restart waiter should be cancelled");
	restart_bridge.stop();
}

void test_teardown_no_hang_on_wedge(const char *argv0)
{
	IpcBridge bridge;
	start_bridge(bridge, argv0, "wedge");
	const auto start = std::chrono::steady_clock::now();
	bridge.stop();
	assert_true(std::chrono::steady_clock::now() - start < 1500ms, "wedged child stop should be finite");
}

void test_sigterm_resistant_child_sigkill(const char *argv0)
{
	IpcBridge bridge;
	auto cfg = cfg_for(argv0, "sigterm_ignore");
	cfg.stop_timeout = 2500ms;
	assert_bridge_started(bridge, cfg, "sigterm-ignore child should start");
	const auto start = std::chrono::steady_clock::now();
	bridge.stop();
	assert_true(std::chrono::steady_clock::now() - start < 3500ms, "SIGKILL reap should keep stop finite");
}

void test_restart_then_stale_caption_reject(const char *argv0)
{
	IpcBridge bridge;
	std::atomic<int> seen{0};
	bridge.set_caption_callback([&](const obs_native_ipc::CaptionEvent &) { seen.fetch_add(1); });
	start_bridge(bridge, argv0, "basic");
	const auto old_epoch = bridge.active_epoch();
	bridge.test_pause_before_respawn(true);
	bridge.test_request_restart();
	assert_true(bridge.test_wait_before_respawn(kWaitTimeout), "restart should advance epoch before join/respawn");
	bridge.test_inject_caption(old_epoch, old_epoch, 111u, "stale");
	assert_true(seen.load() == 0, "stale caption during teardown should be rejected");
	bridge.test_release_before_respawn();
	bridge.stop();
}

void test_stale_error_status_no_effect(const char *argv0)
{
	IpcBridge bridge;
	start_bridge(bridge, argv0, "basic");
	const auto old_epoch = bridge.active_epoch();
	bridge.test_pause_before_respawn(true);
	bridge.test_request_restart();
	assert_true(bridge.test_wait_before_respawn(kWaitTimeout), "restart should pause");
	const auto after_teardown_count = bridge.test_teardown_count();
	bridge.test_inject_status(old_epoch, 1u, 77u);
	std::this_thread::sleep_for(50ms);
	assert_true(bridge.test_teardown_count() == after_teardown_count, "stale error STATUS should not trigger another teardown");
	bridge.test_release_before_respawn();
	bridge.stop();
}

void test_restart_keeps_ring_clears_controlqueue(const char *argv0)
{
	IpcBridge bridge;
	start_bridge(bridge, argv0, "no_control_reply");
	const auto capacity = bridge.test_ring_capacity();
	auto waiter = bridge.test_enqueue_control(ControlCommand::Reconfigure);
	wait_in_flight(bridge, ControlCommand::Reconfigure);
	bridge.test_request_restart();
	assert_true(wait_until(kShortWaitTimeout, [&]() { return waiter->done(); }), "restart should cancel old control queue");
	assert_true(waiter->reason() == WaitReason::Cancelled, "old control should be cancelled");
	assert_true(bridge.test_ring_capacity() == capacity, "restart should keep the same ring allocation live");
	bridge.stop();
}

void test_single_consumer_handoff(const char *argv0)
{
	IpcBridge bridge;
	start_bridge(bridge, argv0, "basic");
	bridge.test_pause_before_respawn(true);
	bridge.test_request_restart();
	assert_true(bridge.test_wait_before_respawn(kWaitTimeout), "restart should wait at handoff gate");
	assert_true(!bridge.test_session_running(), "old writer should be joined before respawn gate");
	bridge.test_release_before_respawn();
	assert_true(wait_until(kWaitTimeout, [&]() { return bridge.test_session_running(); }), "new single consumer should start after gate");
	bridge.stop();
}

void test_degraded_state_transition(const char *argv0)
{
	IpcBridge bridge;
	auto cfg = cfg_for(argv0, "basic");
	cfg.stop_timeout = 40ms;
	assert_bridge_started(bridge, cfg, "bridge should start for degraded test");
	bridge.test_pause_after_producer_enter(true);
	std::vector<float> audio(320, 0.2f);
	std::thread producer([&]() { bridge.push_audio(audio.data(), audio.size(), 1); });
	assert_true(bridge.test_wait_after_producer_enter(kShortWaitTimeout), "producer should pause");
	bridge.stop();
	assert_true(bridge.test_terminal_state() == 2u, "quiesce timeout should enter DEGRADED");
	assert_true(!bridge.test_ring_released(), "degraded should leak ring instead of UAF-prone release");
	bridge.test_release_after_producer_enter();
	producer.join();
}

void test_degraded_detach_preserves_shared_members(const char *argv0)
{
	IpcBridge bridge;
	auto cfg = cfg_for(argv0, "basic");
	cfg.stop_timeout = 40ms;
	assert_bridge_started(bridge, cfg, "bridge should start for detached degraded test");
	const auto ring_capacity = bridge.test_ring_capacity();
	const auto queue_resets = bridge.test_control_queue_reset_count();
	bridge.test_pause_writer_before_exit(true);
	std::thread stopper([&]() { bridge.stop(); });
	assert_true(bridge.test_wait_writer_before_exit(kShortWaitTimeout), "writer should pause before exit");
	stopper.join();
	assert_true(bridge.test_terminal_state() == 2u, "detached worker should leave bridge DEGRADED");
	assert_true(!bridge.test_ring_released(), "detached worker should keep ring leaked");
	assert_true(bridge.test_ring_capacity() == ring_capacity, "degraded ring allocation should remain live");
	assert_true(bridge.test_control_queue_reset_count() == queue_resets, "degraded queue should not be reconstructed");
	bridge.test_release_writer_before_exit();
	assert_true(bridge.test_wait_writer_exited(kShortWaitTimeout), "released detached writer should exit before test ends");
}

void test_flush_done_timeout_order(const char *argv0)
{
	IpcBridge bridge;
	start_bridge(bridge, argv0, "no_control_reply");
	const auto seq = bridge.test_next_control_seq();
	auto waiter = bridge.test_enqueue_control(ControlCommand::Flush);
	wait_in_flight(bridge, ControlCommand::Flush);
	bridge.test_inject_flush_done(bridge.active_epoch(), seq);
	assert_true(waiter->reason() == WaitReason::Complete, "FLUSH_DONE should complete first waiter exactly once");
	bridge.test_timeout_control(ControlCommand::Flush, seq);
	assert_true(waiter->reason() == WaitReason::Complete, "timeout after completion should not overwrite result");
	bridge.stop();
}

int main(int argc, char **argv)
{
	if (argc == 3 && std::strcmp(argv[1], "--fake-child") == 0) {
#ifdef _WIN32
		_setmode(_fileno(stdin), _O_BINARY);
		_setmode(_fileno(stdout), _O_BINARY);
#endif
		return run_fake_child(argv[2]);
	}
	assert_true(argc >= 1, "argv0 exists");
	test_concurrent_duplicate_triggers(argv[0]);
	test_restart_then_destroy(argv[0]);
	test_destroy_then_restart(argv[0]);
	test_destroy_arriving_mid_clear_respawn(argv[0]);
	test_quiesce_before_ring_release(argv[0]);
	test_pending_waiter_cancel_on_destroy_and_restart(argv[0]);
	test_teardown_no_hang_on_wedge(argv[0]);
	test_sigterm_resistant_child_sigkill(argv[0]);
	test_restart_then_stale_caption_reject(argv[0]);
	test_stale_error_status_no_effect(argv[0]);
	test_restart_keeps_ring_clears_controlqueue(argv[0]);
	test_single_consumer_handoff(argv[0]);
	test_degraded_state_transition(argv[0]);
	test_degraded_detach_preserves_shared_members(argv[0]);
	test_flush_done_timeout_order(argv[0]);
	std::cout << "ipc_bridge_teardown_test: PASS" << std::endl;
	return 0;
}
