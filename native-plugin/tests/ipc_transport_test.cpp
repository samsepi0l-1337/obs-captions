// SPDX-License-Identifier: GPL-2.0
#include "ipc-transport.hpp"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

#ifndef __has_feature
#define __has_feature(x) 0
#endif

#ifndef _WIN32
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace {

#if defined(__SANITIZE_THREAD__) || __has_feature(thread_sanitizer)
constexpr bool kThreadSanitizer = true;
#else
constexpr bool kThreadSanitizer = false;
#endif

void assert_true(bool cond, const char* msg)
{
	if (!cond) {
		std::cerr << "FAILED: " << msg << std::endl;
		std::exit(1);
	}
}

bool wait_for_done(std::atomic<bool>& flag, int timeout_ms)
{
	const auto deadline = std::chrono::steady_clock::now() +
			      std::chrono::milliseconds(timeout_ms);
	while (std::chrono::steady_clock::now() < deadline) {
		if (flag.load(std::memory_order_acquire)) {
			return true;
		}
		std::this_thread::sleep_for(std::chrono::milliseconds(2));
	}
	return flag.load(std::memory_order_acquire);
}

obs_native_ipc::SpawnConfig fake_config(const char* argv0, const char* mode)
{
	return obs_native_ipc::SpawnConfig{
		{std::string(argv0), "--fake-child", std::string(mode)},
	};
}

int run_fake_child_echo()
{
	std::vector<std::uint8_t> buffer(4096, 0);
	while (true) {
#ifdef _WIN32
		const std::size_t n = static_cast<std::size_t>(fread(buffer.data(), 1u, buffer.size(), stdin));
#else
		const ssize_t raw = read(STDIN_FILENO, buffer.data(), buffer.size());
		if (raw <= 0) {
			return 0;
		}
		const std::size_t n = static_cast<std::size_t>(raw);
#endif
		if (n == 0) {
			return 0;
		}
		std::size_t offset = 0;
		while (offset < n) {
			const std::size_t written = fwrite(buffer.data() + offset, 1u, n - offset, stdout);
			if (written == 0) {
				return 0;
			}
			offset += written;
		}
		fflush(stdout);
	}
}

int run_fake_child_wedge()
{
	while (true) {
		std::this_thread::sleep_for(std::chrono::milliseconds(100));
	}
}

int run_fake_child_eof()
{
	return 0;
}

int run_fake_child_slowloris()
{
	while (true) {
		std::this_thread::sleep_for(std::chrono::milliseconds(100));
	}
}

void test_round_trip(const char* argv0)
{
	using namespace obs_native_ipc;
	ChildTransport transport;
	assert_true(transport.spawn(fake_config(argv0, "echo")), "spawn echo child should pass");

	const std::string payload = "row5a-transport";
	assert_true(transport.write_all(reinterpret_cast<const std::uint8_t*>(payload.data()), payload.size()),
		    "write_all to echo child should pass");

	std::vector<std::uint8_t> out(payload.size(), 0);
	std::size_t offset = 0;
	const auto start = std::chrono::steady_clock::now();
	while (offset < out.size()) {
		const auto n = transport.read_some(out.data() + offset, out.size() - offset);
		assert_true(n > 0, "round trip read should return bytes");
		offset += static_cast<std::size_t>(n);
		assert_true(std::chrono::steady_clock::now() - start < std::chrono::milliseconds(2000),
			    "round trip read should be timely");
	}
	assert_true(out == std::vector<std::uint8_t>(payload.begin(), payload.end()),
		    "round trip payload should match");

	transport.cancel();
	assert_true(transport.reap() != -1, "round trip reap should return exit code");
}

void test_cancel_unblocks_reader(const char* argv0)
{
	using namespace obs_native_ipc;
	ChildTransport transport;
	assert_true(transport.spawn(fake_config(argv0, "wedge")), "spawn wedge child should pass");

	std::atomic<bool> done{false};
	std::ptrdiff_t value = -1;
	std::thread reader([&]() {
		std::uint8_t buf[32]{};
		value = transport.read_some(buf, sizeof(buf));
		done.store(true, std::memory_order_release);
	});

	std::this_thread::sleep_for(std::chrono::milliseconds(150));
	transport.cancel();
	assert_true(wait_for_done(done, 2000), "reader should unblock after cancel");
	assert_true(value == 0, "reader unblock should return EOF/cancel code");
	assert_true(transport.reap() != -1, "reader cancel reap should return exit code");
	reader.join();
}

void test_cancel_unblocks_writer(const char* argv0)
{
	using namespace obs_native_ipc;
	ChildTransport transport;
	assert_true(transport.spawn(fake_config(argv0, "slowloris")), "spawn slowloris child should pass");

	const std::vector<std::uint8_t> payload(static_cast<std::size_t>(8u) * 1024u * 1024u, 'X');
	std::atomic<bool> done{false};
	std::atomic<bool> success{true};
	std::thread writer([&]() {
		success.store(transport.write_all(payload.data(), payload.size()), std::memory_order_release);
		done.store(true, std::memory_order_release);
	});

	std::this_thread::sleep_for(std::chrono::milliseconds(150));
	transport.cancel();
	assert_true(wait_for_done(done, 2000), "writer should unblock after cancel");
	assert_true(!success.load(std::memory_order_acquire), "writer should return false after cancel");
	assert_true(transport.reap() != -1, "writer cancel reap should return exit code");
	writer.join();
}

void test_eof_detect(const char* argv0)
{
	using namespace obs_native_ipc;
	ChildTransport transport;
	assert_true(transport.spawn(fake_config(argv0, "eof")), "spawn eof child should pass");
	std::uint8_t buf[16]{};
	const auto n = transport.read_some(buf, sizeof(buf));
	assert_true(n == 0, "eof mode should return 0");
	assert_true(transport.reap() != -1, "eof child should be reaped");
}

void test_alive_then_reap(const char* argv0)
{
	using namespace obs_native_ipc;
	ChildTransport transport;
	assert_true(transport.spawn(fake_config(argv0, "eof")), "spawn eof child should pass");

	bool done = false;
	const auto deadline = std::chrono::steady_clock::now() +
			      std::chrono::milliseconds(kThreadSanitizer ? 5000 : 500);
	while (std::chrono::steady_clock::now() < deadline) {
		if (!transport.alive()) {
			done = true;
			break;
		}
		std::this_thread::sleep_for(std::chrono::milliseconds(10));
	}
	assert_true(done, "alive() should observe terminated child");
	assert_true(transport.reap() == 0, "reap() after alive() should return real exit code");
}

void test_reap_no_zombie(const char* argv0)
{
	using namespace obs_native_ipc;
	ChildTransport transport;
	assert_true(transport.spawn(fake_config(argv0, "wedge")), "spawn wedge child should pass");
	transport.cancel();
	const int first = transport.reap();
	assert_true(first != -1, "reap should return a code");
	assert_true(transport.reap() == first, "reap should be idempotent");

{
		ChildTransport orphan;
		assert_true(orphan.spawn(fake_config(argv0, "eof")), "spawn eof child should pass");
	}

#ifndef _WIN32
	int status = 0;
	const pid_t stale = waitpid(-1, &status, WNOHANG);
	assert_true(stale == -1, "destructor should not leave zombies");
#endif
}

int run_fake_child(const std::string& mode)
{
	if (mode == "echo") {
		return run_fake_child_echo();
	}
	if (mode == "wedge") {
		return run_fake_child_wedge();
	}
	if (mode == "eof") {
		return run_fake_child_eof();
	}
	if (mode == "slowloris") {
		return run_fake_child_slowloris();
	}
	return 1;
}

} // namespace

int main(int argc, char** argv)
{
	if (argc == 3 && std::strcmp(argv[1], "--fake-child") == 0) {
		return run_fake_child(argv[2]);
	}

	assert_true(argc >= 1, "argv0 should exist");
	test_round_trip(argv[0]);
	test_cancel_unblocks_reader(argv[0]);
	test_cancel_unblocks_writer(argv[0]);
	test_eof_detect(argv[0]);
	test_alive_then_reap(argv[0]);
	test_reap_no_zombie(argv[0]);

	std::cout << "ipc_transport_test: PASS" << std::endl;
	return 0;
}
