// SPDX-License-Identifier: GPL-2.0
#pragma once

#include <cstddef>
#include <atomic>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace obs_native_ipc {

struct SpawnConfig {
	std::vector<std::string> argv; // [0]=executable, remaining=args
	// Extra (name, value) pairs injected into the child's environment (added
	// to, not replacing, the inherited parent environment). Used to pass
	// cloud STT API keys to the sidecar without writing them to disk.
	std::vector<std::pair<std::string, std::string>> env;
};

class ChildTransport {
public:
	ChildTransport() = default;
	~ChildTransport();
	ChildTransport(const ChildTransport&) = delete;
	ChildTransport& operator=(const ChildTransport&) = delete;
	ChildTransport(ChildTransport&&) noexcept;
	ChildTransport& operator=(ChildTransport&&) noexcept;

	bool spawn(const SpawnConfig& cfg);
	bool write_all(const std::uint8_t* data, std::size_t n);
	std::ptrdiff_t read_some(std::uint8_t* buf, std::size_t n);
	void cancel();
	int reap();
	bool alive() noexcept;

private:
#ifdef _WIN32
	struct WinState {
		void* child_process{nullptr};
		void* child_thread{nullptr};
		void* stdin_write{nullptr};
		void* stdout_read{nullptr};
	};
	void clear() noexcept;
	void close_resources() noexcept;
	WinState state_{};
#else
	struct State {
		std::atomic<int> write_fd{-1};
		std::atomic<int> read_fd{-1};
		std::atomic<int> child_pid{-1};
	};
	State state_{};
	void clear() noexcept;
	void close_resources() noexcept;
#endif
	std::atomic<bool> started_{false};
	std::atomic<bool> cancelled_{false};
	std::atomic<bool> reaped_{false};
	int exit_code_{-1};
};

} // namespace obs_native_ipc
