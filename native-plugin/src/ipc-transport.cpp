// SPDX-License-Identifier: GPL-2.0
#include "ipc-transport.hpp"

#include <algorithm>
#include <chrono>
#include <cerrno>
#include <cstdlib>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#ifndef _WIN32
#include <csignal>
#include <fcntl.h>
#include <signal.h>
#include <sys/wait.h>
#include <unistd.h>
#else
#include <cstring>
#include <windows.h>
#endif

namespace {

#ifndef _WIN32
void ignore_sigpipe()
{
	static std::once_flag flag;
	std::call_once(flag, []() { std::signal(SIGPIPE, SIG_IGN); });
}
void close_fd(int& fd)
{
	if (fd >= 0) {
		close(fd);
		fd = -1;
	}
}
void close_fd(std::atomic<int>& fd)
{
	const int handle = fd.exchange(-1);
	if (handle >= 0) {
		close(handle);
	}
}
#else
void close_handle(HANDLE& h)
{
	if (h != nullptr) {
		CloseHandle(h);
		h = nullptr;
	}
}
bool set_inheritable(HANDLE h, bool inheritable)
{
	return SetHandleInformation(h, HANDLE_FLAG_INHERIT, inheritable ? HANDLE_FLAG_INHERIT : 0) != FALSE;
}
std::string quote_arg(const std::string& arg)
{
	if (arg.empty() || arg.find_first_of(" \t\"") == std::string::npos) {
		return arg;
	}
	std::string out{"\""};
	for (char c : arg) {
		if (c == '"') {
			out.push_back('\\');
		}
		out.push_back(c);
	}
	out.push_back('\"');
	return out;
}

bool same_env_name(const std::string& entry, const std::string& name)
{
	return entry.size() > name.size() && entry[name.size()] == '=' &&
	       _strnicmp(entry.c_str(), name.c_str(), name.size()) == 0;
}

// Builds a merged ANSI environment block (parent environment plus/overriding
// `overrides`) suitable for CreateProcessA's lpEnvironment. Returns an empty
// string when there is nothing to override, in which case the caller should
// pass nullptr so the child simply inherits the parent's environment as
// before this change.
std::string build_env_block(const std::vector<std::pair<std::string, std::string>>& overrides)
{
	if (overrides.empty()) {
		return {};
	}

	std::vector<std::string> entries;
	if (char* base = GetEnvironmentStringsA()) {
		for (const char* p = base; *p != '\0';) {
			std::string entry(p);
			p += entry.size() + 1;
			entries.push_back(std::move(entry));
		}
		FreeEnvironmentStringsA(base);
	}

	for (const auto& kv : overrides) {
		entries.erase(std::remove_if(entries.begin(), entries.end(),
					      [&kv](const std::string& entry) { return same_env_name(entry, kv.first); }),
			      entries.end());
		entries.push_back(kv.first + "=" + kv.second);
	}

	std::string block;
	for (const auto& entry : entries) {
		block += entry;
		block.push_back('\0');
	}
	block.push_back('\0');
	return block;
}
#endif

} // namespace

namespace obs_native_ipc {

void ChildTransport::close_resources() noexcept
{
#ifndef _WIN32
	close_fd(state_.write_fd);
	close_fd(state_.read_fd);
#else
	close_handle(state_.stdin_write);
	close_handle(state_.stdout_read);
	close_handle(state_.child_thread);
	close_handle(state_.child_process);
#endif
}

void ChildTransport::clear() noexcept
{
	close_resources();
#ifndef _WIN32
	state_.child_pid.store(-1);
#endif
	started_.store(false);
	cancelled_.store(false);
	reaped_.store(true);
}

ChildTransport::~ChildTransport()
{
	cancel();
	(void)reap();
}

ChildTransport::ChildTransport(ChildTransport&& rhs) noexcept
{
	*this = std::move(rhs);
}

ChildTransport& ChildTransport::operator=(ChildTransport&& rhs) noexcept
{
	if (this == &rhs) {
		return *this;
	}
	cancel();
	(void)reap();
	clear();
	started_.store(rhs.started_.load(std::memory_order_acquire));
	cancelled_.store(rhs.cancelled_.load(std::memory_order_acquire));
	reaped_.store(rhs.reaped_.load(std::memory_order_acquire));
	rhs.started_.store(false);
	rhs.cancelled_.store(false);
	rhs.reaped_.store(true);
	std::swap(exit_code_, rhs.exit_code_);
#ifndef _WIN32
	state_.write_fd.store(rhs.state_.write_fd.exchange(-1));
	state_.read_fd.store(rhs.state_.read_fd.exchange(-1));
	state_.child_pid.store(rhs.state_.child_pid.exchange(-1));
	rhs.clear();
#else
	std::swap(state_.child_process, rhs.state_.child_process);
	std::swap(state_.child_thread, rhs.state_.child_thread);
	std::swap(state_.stdin_write, rhs.state_.stdin_write);
	std::swap(state_.stdout_read, rhs.state_.stdout_read);
	rhs.started_.store(false);
	rhs.cancelled_.store(false);
	rhs.reaped_.store(true);
	rhs.state_.child_process = nullptr;
	rhs.state_.child_thread = nullptr;
	rhs.state_.stdin_write = nullptr;
	rhs.state_.stdout_read = nullptr;
#endif
	return *this;
}

bool ChildTransport::spawn(const SpawnConfig& cfg)
{
	if (cfg.argv.empty() || cfg.argv[0].empty()) {
		return false;
	}
	cancel();
	(void)reap();
	clear();

#ifndef _WIN32
	ignore_sigpipe();
	int in_pipe[2]{-1, -1};
	int out_pipe[2]{-1, -1};
	if (pipe(in_pipe) != 0 || pipe(out_pipe) != 0) {
		close_fd(in_pipe[0]);
		close_fd(in_pipe[1]);
		close_fd(out_pipe[0]);
		close_fd(out_pipe[1]);
		return false;
	}
	const int pid = fork();
	if (pid < 0) {
		close_fd(in_pipe[0]);
		close_fd(in_pipe[1]);
		close_fd(out_pipe[0]);
		close_fd(out_pipe[1]);
		return false;
	}
	if (pid == 0) {
		dup2(in_pipe[0], STDIN_FILENO);
		dup2(out_pipe[1], STDOUT_FILENO);
		const int dev_null = open("/dev/null", O_WRONLY);
		if (dev_null >= 0) {
			dup2(dev_null, STDERR_FILENO);
			close(dev_null);
		}
		close_fd(in_pipe[1]);
		close_fd(in_pipe[0]);
		close_fd(out_pipe[0]);
		close_fd(out_pipe[1]);
		for (const auto& kv : cfg.env) {
			setenv(kv.first.c_str(), kv.second.c_str(), 1);
		}
		std::vector<char*> argv;
		argv.reserve(cfg.argv.size() + 1u);
		for (const auto& arg : cfg.argv) {
			argv.push_back(const_cast<char*>(arg.c_str()));
		}
		argv.push_back(nullptr);
		execvp(argv[0], argv.data());
		_exit(127);
	}
	state_.child_pid.store(pid);
	state_.write_fd.store(in_pipe[1]);
	state_.read_fd.store(out_pipe[0]);
	close_fd(in_pipe[0]);
	close_fd(out_pipe[1]);
#else
	std::string cmd_line;
	for (std::size_t i = 0; i < cfg.argv.size(); ++i) {
		if (i > 0) cmd_line.push_back(' ');
		cmd_line += quote_arg(cfg.argv[i]);
	}
	if (cmd_line.empty()) return false;
	std::vector<char> cmd(cmd_line.begin(), cmd_line.end());
	cmd.push_back('\0');

	SECURITY_ATTRIBUTES sa{};
	sa.nLength = sizeof(sa);
	sa.bInheritHandle = TRUE;
	sa.lpSecurityDescriptor = nullptr;
	HANDLE in_read = nullptr, in_write = nullptr, out_read = nullptr, out_write = nullptr;
	if (!CreatePipe(&in_read, &in_write, &sa, 0) || !CreatePipe(&out_read, &out_write, &sa, 0)) {
		close_handle(in_read);
		close_handle(in_write);
		close_handle(out_read);
		close_handle(out_write);
		return false;
	}
	if (!set_inheritable(in_read, true) || !set_inheritable(out_write, true) ||
	    !set_inheritable(in_write, false) || !set_inheritable(out_read, false)) {
		close_handle(in_read);
		close_handle(in_write);
		close_handle(out_read);
		close_handle(out_write);
		return false;
	}
	STARTUPINFOA si{};
	si.cb = sizeof(si);
	si.dwFlags = STARTF_USESTDHANDLES;
	si.hStdInput = in_read;
	si.hStdOutput = out_write;
	si.hStdError = out_write;
	PROCESS_INFORMATION pi{};
	std::string env_block = build_env_block(cfg.env);
	void* env_ptr = env_block.empty() ? nullptr : static_cast<void*>(env_block.data());
	if (!CreateProcessA(nullptr, cmd.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW, env_ptr, nullptr, &si, &pi)) {
		close_handle(in_read);
		close_handle(in_write);
		close_handle(out_read);
		close_handle(out_write);
		return false;
	}
	state_.child_process = pi.hProcess;
	state_.child_thread = pi.hThread;
	state_.stdin_write = in_write;
	state_.stdout_read = out_read;
	close_handle(in_read);
	close_handle(out_write);
#endif
	started_.store(true);
	cancelled_.store(false);
	reaped_.store(false);
	exit_code_ = -1;
	return true;
}

bool ChildTransport::write_all(const std::uint8_t* data, std::size_t n)
{
	if (!started_.load(std::memory_order_acquire) || cancelled_.load(std::memory_order_acquire) || reaped_.load(std::memory_order_acquire) ||
	    data == nullptr || n == 0u) {
		return false;
	}
#ifndef _WIN32
	const int fd = state_.write_fd.load(std::memory_order_acquire);
	if (fd < 0) return false;
	std::size_t off = 0;
	while (off < n) {
		const std::size_t chunk = std::min<std::size_t>(n - off, 0x10000u);
		const ssize_t w = write(fd, data + off, chunk);
		if (w > 0) {
			off += static_cast<std::size_t>(w);
			continue;
		}
		if (w < 0 && errno == EINTR) continue;
		return false;
	}
#else
	const HANDLE h = static_cast<HANDLE>(state_.stdin_write);
	if (h == nullptr) return false;
	std::size_t off = 0;
	while (off < n) {
		const std::size_t chunk = std::min<std::size_t>(n - off, 0x10000u);
		DWORD w = 0;
		if (!WriteFile(h, data + off, static_cast<DWORD>(chunk), &w, nullptr)) return false;
		if (w == 0u) return false;
		off += static_cast<std::size_t>(w);
	}
#endif
	return true;
}

std::ptrdiff_t ChildTransport::read_some(std::uint8_t* buf, std::size_t n)
{
	if (buf == nullptr || n == 0u || !started_.load(std::memory_order_acquire) || cancelled_.load(std::memory_order_acquire) ||
	    reaped_.load(std::memory_order_acquire)) {
		return -1;
	}
#ifndef _WIN32
	const int fd = state_.read_fd.load(std::memory_order_acquire);
	if (fd < 0) return 0;
	while (true) {
		const ssize_t r = read(fd, buf, n);
		if (r > 0) return r;
		if (r == 0) return 0;
		if (errno == EINTR) continue;
		return (errno == EBADF || errno == EPIPE) ? 0 : -1;
	}
#else
	const HANDLE h = static_cast<HANDLE>(state_.stdout_read);
	if (h == nullptr) return 0;
	DWORD r = 0;
	if (ReadFile(h, buf, static_cast<DWORD>(n), &r, nullptr)) return static_cast<std::ptrdiff_t>(r);
	const DWORD e = GetLastError();
	return (e == ERROR_BROKEN_PIPE || e == ERROR_HANDLE_EOF || e == ERROR_INVALID_HANDLE) ? 0 : -1;
#endif
}

void ChildTransport::cancel()
{
	if (!started_.load(std::memory_order_acquire) || cancelled_.load(std::memory_order_acquire)) return;
	cancelled_.store(true);
#ifndef _WIN32
	close_fd(state_.write_fd);
	close_fd(state_.read_fd);
	const int pid = state_.child_pid.load(std::memory_order_acquire);
	if (pid > 0) {
		kill(pid, SIGTERM);
	}
#else
	if (state_.child_process != nullptr) TerminateProcess(state_.child_process, 1);
	close_handle(state_.stdin_write);
	close_handle(state_.stdout_read);
#endif
}

int ChildTransport::reap()
{
	if (reaped_.load(std::memory_order_acquire)) return exit_code_;
	if (!started_.load(std::memory_order_acquire)) {
		clear();
		return -1;
	}
#ifndef _WIN32
	const int pid = state_.child_pid.load(std::memory_order_acquire);
	if (pid <= 0) {
		clear();
		return -1;
	}
	int status = -1, done = -1;
	if (!cancelled_.load(std::memory_order_acquire)) {
		do {
			done = waitpid(pid, &status, 0);
		} while (done < 0 && errno == EINTR);
	} else {
		const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(1800);
		while ((done = waitpid(pid, &status, WNOHANG)) == 0) {
			if (std::chrono::steady_clock::now() >= deadline) break;
			std::this_thread::sleep_for(std::chrono::milliseconds(5));
		}
		if (done == 0) {
			kill(pid, SIGKILL);
			do {
				done = waitpid(pid, &status, 0);
			} while (done < 0 && errno == EINTR);
		}
	}
	if (done == pid) {
		exit_code_ = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
	} else {
		exit_code_ = -1;
	}
	clear();
	return exit_code_;
#else
	if (state_.child_process == nullptr) {
		clear();
		return -1;
	}
	DWORD wait = WaitForSingleObject(state_.child_process, 2000);
	if (wait == WAIT_TIMEOUT && state_.child_process != nullptr) {
		TerminateProcess(state_.child_process, 1);
		wait = WaitForSingleObject(state_.child_process, 2000);
	}
	if (wait != WAIT_OBJECT_0) {
		exit_code_ = -1;
		clear();
		return exit_code_;
	}
	DWORD code = 0;
	if (!GetExitCodeProcess(state_.child_process, &code)) {
		exit_code_ = -1;
		clear();
		return exit_code_;
	}
	exit_code_ = static_cast<int>(code);
	clear();
	return exit_code_;
#endif
}

bool ChildTransport::alive() noexcept
{
	if (!started_.load(std::memory_order_acquire) || cancelled_.load(std::memory_order_acquire) || reaped_.load(std::memory_order_acquire)) {
		return false;
	}
#ifndef _WIN32
	const int pid = state_.child_pid.load(std::memory_order_acquire);
	if (pid <= 0) return false;
	int status = 0;
	const int done = waitpid(pid, &status, WNOHANG);
	if (done == 0) return true;
	if (done == pid) {
		exit_code_ = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
	} else {
		exit_code_ = -1;
	}
	clear();
	return false;
#else
	DWORD code = 0;
	return state_.child_process != nullptr && GetExitCodeProcess(state_.child_process, &code) && code == STILL_ACTIVE;
#endif
}

} // namespace obs_native_ipc
