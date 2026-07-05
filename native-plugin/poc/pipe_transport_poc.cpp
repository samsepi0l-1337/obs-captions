/* SPDX-License-Identifier: GPL-2.0 */
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <csignal>
#include <deque>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <condition_variable>
#include <mutex>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#else
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace
{
struct CaseResult {
    std::string id;
    bool pass = false;
    long long unblocked_ms = -1;
    int child_exit_code = -1;
    bool child_reaped = false;
    bool timed_out = false;
    std::string note;
    std::string mechanism;
};

using clock_t = std::chrono::steady_clock;

long long since_ms(clock_t::time_point start)
{
    return std::chrono::duration_cast<std::chrono::milliseconds>(clock_t::now() - start).count();
}

bool wait_done(std::atomic<bool>& done, int timeout_ms)
{
    auto deadline = clock_t::now() + std::chrono::milliseconds(timeout_ms);
    while (clock_t::now() < deadline) {
        if (done.load(std::memory_order_acquire)) {
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    return false;
}

void print_case(std::string_view platform, const CaseResult& r)
{
    std::cout << "[" << platform << "] " << r.id << ": " << (r.pass ? "PASS" : "FAIL")
              << " (unblocked in " << r.unblocked_ms << "ms)"
              << " mechanism=" << r.mechanism
              << " child_exit=" << r.child_exit_code
              << " reaped=" << (r.child_reaped ? "true" : "false")
              << " note=" << r.note << "\n";
}

void write_decision_file(const std::string& exe_path,
                        const std::vector<CaseResult>& posix_cases,
                        const std::vector<CaseResult>& windows_cases = {})
{
    std::filesystem::path exe_dir = std::filesystem::path(exe_path).parent_path();
    std::ofstream out(exe_dir / "TRANSPORT_DECISION.md");

    out << "# pipe_transport_poc transport decision\n\n";
    out << "- Date: 2026-07-05\n";
    out << "- Branch: feat/settings-gui\n";
    out << "- Scope: native-plugin/poc only\n\n";

    out << "## 플랫폼별 선택 메커니즘\n";
    out << "- POSIX: A (blocking read()/write() 스レッド + 파이프 fd close + SIGTERM/SIGKILL).\n";
    out << "- Windows: A/B 구현 완료 (A: anonymous/anonymous-like pipe + TerminateProcess, B: overlapped named pipe + CancelIoEx). \n";
    out << "- Windows: 실행 환경별로 a/b/c 결과를 아래에 기록.\n\n";

    out << "## POSIX 결과\n";
    out << "| Case | PASS | Unblocked(ms) | Child exit | Reaped | TimedOut | Note |\n";
    out << "| --- | --- | --- | --- | --- | --- | --- |\n";
    for (const auto& c : posix_cases) {
        out << "| " << c.id
            << " | " << (c.pass ? "PASS" : "FAIL")
            << " | " << c.unblocked_ms
            << " | " << c.child_exit_code
            << " | " << (c.child_reaped ? "true" : "false")
            << " | " << (c.timed_out ? "true" : "false")
            << " | " << c.note << " |\n";
    }

    out << "\n## Windows\n";
    if (windows_cases.empty()) {
        out << "- Status: PENDING (현재 Windows 빌드/실행 머신 미연결)\n";
        out << "- case a/b/c: PENDING\n";
        return;
    }

    bool windows_all_pass = true;
    for (const auto& c : windows_cases) {
        if (!c.pass) {
            windows_all_pass = false;
            break;
        }
    }

    out << "- Status: " << (windows_all_pass ? "PASS" : "FAIL") << "\n";
    out << "| Case | PASS | Unblocked(ms) | Child exit | Reaped | TimedOut | Note |\n";
    out << "| --- | --- | --- | --- | --- | --- | --- |\n";
    for (const auto& c : windows_cases) {
        out << "| " << c.id
            << " | " << (c.pass ? "PASS" : "FAIL")
            << " | " << c.unblocked_ms
            << " | " << c.child_exit_code
            << " | " << (c.child_reaped ? "true" : "false")
            << " | " << (c.timed_out ? "true" : "false")
            << " | " << c.note << " |\n";
    }
}

#ifdef _WIN32

struct WinHandle {
    HANDLE handle = nullptr;

    WinHandle() = default;
    explicit WinHandle(HANDLE in_handle) : handle(in_handle) {}
    WinHandle(const WinHandle&) = delete;
    WinHandle& operator=(const WinHandle&) = delete;

    WinHandle(WinHandle&& rhs) noexcept : handle(rhs.handle)
    {
        rhs.handle = nullptr;
    }

    WinHandle& operator=(WinHandle&& rhs) noexcept
    {
        if (this != &rhs) {
            reset();
            handle = rhs.handle;
            rhs.handle = nullptr;
        }
        return *this;
    }

    ~WinHandle()
    {
        reset();
    }

    bool valid() const
    {
        return handle != nullptr && handle != INVALID_HANDLE_VALUE;
    }

    HANDLE get() const
    {
        return handle;
    }

    void reset(HANDLE next = nullptr)
    {
        if (valid()) {
            CloseHandle(handle);
        }
        handle = next;
    }
};

struct NamedPipePair {
    WinHandle child;
    WinHandle parent;
};

struct WindowsTransport {
    bool use_overlapped = false;
    WinHandle process;
    WinHandle thread;
    WinHandle stdin_write_handle;
    WinHandle stdout_read_handle;
    DWORD process_id = 0;
};

static std::string executable_path_windows()
{
    std::vector<char> buffer(512, '\0');
    while (true) {
        DWORD len = GetModuleFileNameA(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
        if (len == 0) {
            return {};
        }
        if (len < buffer.size() - 1) {
            return {buffer.data(), len};
        }
        buffer.resize(buffer.size() * 2, '\0');
    }
}

static std::string next_pipe_name(std::string_view tag)
{
    static std::atomic<unsigned long long> seq{0};
    return std::string{"\\\\.\\pipe\\obs_poc_"} + std::string{tag}
        + "_" + std::to_string(GetCurrentProcessId())
        + "_" + std::to_string(seq.fetch_add(1, std::memory_order_relaxed));
}

static bool set_handle_inheritable(HANDLE h, bool inheritable)
{
    return SetHandleInformation(h, HANDLE_FLAG_INHERIT, inheritable ? HANDLE_FLAG_INHERIT : 0) != FALSE;
}

static bool create_named_pipe_pair(
    std::string_view tag,
    DWORD server_access,
    DWORD client_access,
    NamedPipePair& out_pair)
{
    const std::string pipe_name = next_pipe_name(tag);
    SECURITY_ATTRIBUTES sa{};
    sa.nLength = sizeof(sa);
    sa.lpSecurityDescriptor = nullptr;
    sa.bInheritHandle = TRUE;

    WinHandle server(CreateNamedPipeA(
        pipe_name.c_str(),
        server_access | FILE_FLAG_OVERLAPPED,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
        1,
        65536,
        65536,
        0,
        &sa));
    if (!server.valid()) {
        return false;
    }

    if (!set_handle_inheritable(server.get(), TRUE)) {
        return false;
    }

    WinHandle parent(CreateFileA(
        pipe_name.c_str(),
        client_access,
        0,
        nullptr,
        OPEN_EXISTING,
        FILE_FLAG_OVERLAPPED,
        nullptr));
    if (!parent.valid()) {
        return false;
    }

    OVERLAPPED connect_ov{};
    connect_ov.hEvent = CreateEventA(nullptr, TRUE, FALSE, nullptr);
    if (!connect_ov.hEvent) {
        return false;
    }

    DWORD connect_err = ERROR_SUCCESS;
    BOOL connect_ok = ConnectNamedPipe(server.get(), &connect_ov);
    if (!connect_ok) {
        connect_err = GetLastError();
        if (connect_err != ERROR_PIPE_CONNECTED && connect_err != ERROR_IO_PENDING) {
            CloseHandle(connect_ov.hEvent);
            return false;
        }
    }
    if (connect_err == ERROR_IO_PENDING) {
        DWORD connect_transfer = 0;
        if (!GetOverlappedResult(server.get(), &connect_ov, &connect_transfer, TRUE)) {
            CloseHandle(connect_ov.hEvent);
            return false;
        }
    }

    CloseHandle(connect_ov.hEvent);

    out_pair.child = std::move(server);
    out_pair.parent = std::move(parent);
    return true;
}

static bool spawn_peer_windows(const std::string& self, WindowsTransport& transport, bool use_overlapped)
{
    NamedPipePair stdin_pair;
    NamedPipePair stdout_pair;

    if (use_overlapped) {
        if (!create_named_pipe_pair("stdin", PIPE_ACCESS_INBOUND, GENERIC_WRITE, stdin_pair)) {
            return false;
        }
        if (!create_named_pipe_pair("stdout", PIPE_ACCESS_OUTBOUND, GENERIC_READ, stdout_pair)) {
            return false;
        }
        transport.use_overlapped = true;
    } else {
        SECURITY_ATTRIBUTES sa{};
        sa.nLength = sizeof(sa);
        sa.lpSecurityDescriptor = nullptr;
        sa.bInheritHandle = TRUE;

        HANDLE stdin_to_child = nullptr;    // child reads
        HANDLE stdin_from_parent = nullptr; // parent writes
        HANDLE stdout_to_parent = nullptr;  // parent reads
        HANDLE stdout_from_child = nullptr; // child writes

        if (!CreatePipe(&stdin_to_child, &stdin_from_parent, &sa, 0)) {
            return false;
        }

        if (!CreatePipe(&stdout_to_parent, &stdout_from_child, &sa, 0)) {
            CloseHandle(stdin_to_child);
            CloseHandle(stdin_from_parent);
            return false;
        }

        if (!set_handle_inheritable(stdin_to_child, TRUE) || !set_handle_inheritable(stdout_from_child, TRUE)
            || !set_handle_inheritable(stdin_from_parent, FALSE) || !set_handle_inheritable(stdout_to_parent, FALSE)) {
            CloseHandle(stdin_to_child);
            CloseHandle(stdin_from_parent);
            CloseHandle(stdout_to_parent);
            CloseHandle(stdout_from_child);
            return false;
        }

        stdin_pair.child.reset(stdin_to_child);
        stdin_pair.parent.reset(stdin_from_parent);
        stdout_pair.child.reset(stdout_from_child);
        stdout_pair.parent.reset(stdout_to_parent);
        transport.use_overlapped = false;
    }

    std::string cmd = "\"" + self + "\" --peer";
    std::vector<char> cmd_line(cmd.begin(), cmd.end());
    cmd_line.push_back('\0');

    STARTUPINFOA si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = stdin_pair.child.get();
    si.hStdOutput = stdout_pair.child.get();
    si.hStdError = stdout_pair.child.get();

    PROCESS_INFORMATION pi{};
    if (!CreateProcessA(nullptr, cmd_line.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi)) {
        return false;
    }

    transport.process.reset(pi.hProcess);
    transport.thread.reset(pi.hThread);
    transport.process_id = pi.dwProcessId;
    transport.stdin_write_handle = std::move(stdin_pair.parent);
    transport.stdout_read_handle = std::move(stdout_pair.parent);

    stdin_pair.child.reset();
    stdout_pair.child.reset();
    return true;
}

static bool write_payload(HANDLE out_fd, const char* data, size_t size, bool overlapped_mode)
{
    size_t offset = 0;
    while (offset < size) {
        DWORD chunk = static_cast<DWORD>(std::min<size_t>(size - offset, 4096));
        DWORD written = 0;

        if (overlapped_mode) {
            OVERLAPPED ov{};
            ov.hEvent = CreateEventA(nullptr, TRUE, FALSE, nullptr);
            if (!ov.hEvent) {
                return false;
            }

            BOOL ok = WriteFile(out_fd, data + offset, chunk, nullptr, &ov);
            if (!ok) {
                DWORD err = GetLastError();
                if (err != ERROR_IO_PENDING) {
                    CloseHandle(ov.hEvent);
                    return false;
                }
            }
            if (!GetOverlappedResult(out_fd, &ov, &written, TRUE)) {
                CloseHandle(ov.hEvent);
                return false;
            }

            CloseHandle(ov.hEvent);
            if (written == 0) {
                return false;
            }
        } else {
            BOOL ok = WriteFile(out_fd, data + offset, chunk, &written, nullptr);
            if (!ok || written == 0) {
                return false;
            }
        }

        offset += static_cast<size_t>(written);
    }

    return true;
}

static bool wait_child_exit(WindowsTransport& peer, int timeout_ms, int& exit_code)
{
    exit_code = -1;
    if (!peer.process.valid()) {
        return false;
    }

    DWORD wait = WaitForSingleObject(peer.process.get(), static_cast<DWORD>(timeout_ms));
    if (wait != WAIT_OBJECT_0) {
        return false;
    }

    DWORD code = 0;
    if (!GetExitCodeProcess(peer.process.get(), &code)) {
        return false;
    }
    exit_code = static_cast<int>(code);
    return true;
}

static void request_cancel(HANDLE io_handle, bool overlapped_mode)
{
    if (overlapped_mode && io_handle != nullptr && io_handle != INVALID_HANDLE_VALUE) {
        CancelIoEx(io_handle, nullptr);
    }
}

static CaseResult run_windows_case_once(std::string_view case_id, bool use_overlapped, bool use_stop_queue)
{
    CaseResult r{std::string(case_id), false, -1, -1, false, false,
                 "not run", use_overlapped ? "B" : "A"};

    const std::string self = executable_path_windows();
    if (self.empty()) {
        r.note = "executable path resolution failed";
        return r;
    }

    WindowsTransport peer;
    if (!spawn_peer_windows(self, peer, use_overlapped)) {
        r.note = "spawn failed";
        return r;
    }

    std::atomic<bool> io_done{false};
    std::thread reader_thread;
    std::thread writer_thread;
    std::mutex queue_m;
    std::deque<std::vector<char>> queue;
    size_t queue_size_before = 0;

    if (case_id == "case a") {
        reader_thread = std::thread([&]() {
            std::array<char, 256> buf;
            while (true) {
                DWORD read = 0;
                if (use_overlapped) {
                OVERLAPPED ov{};
                ov.hEvent = CreateEventA(nullptr, TRUE, FALSE, nullptr);
                if (!ov.hEvent) {
                    break;
                }

                BOOL ok = ReadFile(peer.stdout_read_handle.get(), buf.data(), buf.size(), nullptr, &ov);
                if (!ok) {
                    DWORD err = GetLastError();
                    if (err != ERROR_IO_PENDING) {
                        CloseHandle(ov.hEvent);
                        break;
                    }
                }
                if (!GetOverlappedResult(peer.stdout_read_handle.get(), &ov, &read, TRUE)) {
                    CloseHandle(ov.hEvent);
                    break;
                }
                CloseHandle(ov.hEvent);
                } else {
                    if (!ReadFile(peer.stdout_read_handle.get(), buf.data(), buf.size(), &read, nullptr)) {
                        break;
                    }
                }

                if (read == 0) {
                    break;
                }
            }
            io_done.store(true, std::memory_order_release);
        });
    } else if (case_id == "case c" && use_stop_queue) {
        queue.emplace_back(1024 * 1024, 'X');
        queue.emplace_back(std::vector<char>{'s', 't', 'o', 'p'});

        writer_thread = std::thread([&]() {
            while (true) {
                std::vector<char> frame;
                {
                    std::lock_guard lock(queue_m);
                    if (queue.empty()) {
                        break;
                    }
                    frame = std::move(queue.front());
                    queue.pop_front();
                }

                if (!write_payload(peer.stdin_write_handle.get(), frame.data(), frame.size(), use_overlapped)) {
                    break;
                }
            }
            io_done.store(true, std::memory_order_release);
        });
    } else {
        writer_thread = std::thread([&]() {
            const char byte = 'X';
            while (true) {
                if (!write_payload(peer.stdin_write_handle.get(), &byte, sizeof(byte), use_overlapped)) {
                    break;
                }
            }
            io_done.store(true, std::memory_order_release);
        });
    }

    std::this_thread::sleep_for(std::chrono::milliseconds(150));

    auto start = clock_t::now();
    if (case_id == "case c" && use_stop_queue) {
        std::lock_guard lock(queue_m);
        queue_size_before = queue.size();
    }
    if (peer.process.valid()) {
        TerminateProcess(peer.process.get(), 1);
    }

    bool unblocked = wait_done(io_done, 2000);
    if (!unblocked && use_overlapped) {
        request_cancel(peer.stdin_write_handle.get(), true);
        request_cancel(peer.stdout_read_handle.get(), true);
        unblocked = wait_done(io_done, 2000);
    }

    if (!unblocked) {
        peer.stdin_write_handle.reset();
        peer.stdout_read_handle.reset();
        request_cancel(nullptr, false);
        unblocked = wait_done(io_done, 1000);
    }

    r.unblocked_ms = since_ms(start);
    r.timed_out = !unblocked;
    r.child_reaped = wait_child_exit(peer, 2000, r.child_exit_code);
    r.pass = unblocked && r.child_reaped;

    if (case_id == "case c") {
        if (queue_size_before > 0) {
            r.note = "stop frame remained queued during teardown";
        } else {
            r.note = "stop frame missing before teardown";
        }
    } else if (case_id == "case a") {
        r.note = "reader blocked in ReadFile";
    } else {
        r.note = "writer blocked in WriteFile";
    }

    if (r.timed_out) {
        r.note += ", timed out waiting for cancellation";
    }

    if (!r.child_reaped) {
        r.note += ", child did not exit";
    }

    if (reader_thread.joinable()) {
        reader_thread.join();
    }
    if (writer_thread.joinable()) {
        writer_thread.join();
    }

    return r;
}

static CaseResult run_windows_case(std::string_view case_id, bool queue_stop_frame)
{
    CaseResult first = run_windows_case_once(case_id, false, queue_stop_frame);
    if (first.pass) {
        return first;
    }

    CaseResult second = run_windows_case_once(case_id, true, queue_stop_frame);
    if (second.pass) {
        second.note = "A failed; " + first.note + " | " + second.note;
        second.mechanism = "B";
        return second;
    }

    second.note = "A failed; " + first.note + " | B failed; " + second.note;
    second.mechanism = "A/B";
    return second;
}

int run_peer_mode_windows()
{
    while (true) {
        Sleep(100);
    }
}

std::vector<CaseResult> run_all_windows()
{
    std::vector<CaseResult> results;
    results.push_back(run_windows_case("case a", false));
    results.push_back(run_windows_case("case b", false));
    results.push_back(run_windows_case("case c", true));
    return results;
}

#else

static bool terminate_and_wait_child(pid_t pid, int timeout_ms, int& exit_code)
{
    int status = -1;
    exit_code = -1;
    if (pid <= 0) {
        return false;
    }

    kill(pid, SIGTERM);
    auto deadline = clock_t::now() + std::chrono::milliseconds(timeout_ms);
    while (clock_t::now() < deadline) {
        pid_t done = waitpid(pid, &status, WNOHANG);
        if (done == pid) {
            if (WIFEXITED(status)) {
                exit_code = WEXITSTATUS(status);
            } else if (WIFSIGNALED(status)) {
                exit_code = 128 + WTERMSIG(status);
            }
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }

    kill(pid, SIGKILL);
    auto deadline2 = clock_t::now() + std::chrono::milliseconds(200);
    while (clock_t::now() < deadline2) {
        pid_t done2 = waitpid(pid, &status, WNOHANG);
        if (done2 == pid) {
            if (WIFEXITED(status)) {
                exit_code = WEXITSTATUS(status);
            } else if (WIFSIGNALED(status)) {
                exit_code = 128 + WTERMSIG(status);
            }
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    return false;
}

struct PosixScopedFd {
    int fd = -1;

    explicit PosixScopedFd(int in_fd = -1) : fd(in_fd) {}

    ~PosixScopedFd()
    {
        if (fd >= 0) {
            close(fd);
        }
    }

    PosixScopedFd(const PosixScopedFd&) = delete;
    PosixScopedFd& operator=(const PosixScopedFd&) = delete;

    PosixScopedFd(PosixScopedFd&& rhs) noexcept : fd(rhs.fd) { rhs.fd = -1; }

    PosixScopedFd& operator=(PosixScopedFd&& rhs) noexcept
    {
        if (this != &rhs) {
            if (fd >= 0) {
                close(fd);
            }
            fd = rhs.fd;
            rhs.fd = -1;
        }
        return *this;
    }

    int release()
    {
        int out = fd;
        fd = -1;
        return out;
    }
};

struct PosixChildProc {
    pid_t pid{-1};
    PosixScopedFd write_fd; // parent writes to this fd -> child stdin
    PosixScopedFd read_fd;  // parent reads from this fd <- child stdout
};

static std::string executable_path(const char* argv0)
{
    char* resolved = realpath(argv0, nullptr);
    if (resolved) {
        std::string out(resolved);
        free(resolved);
        return out;
    }
    return std::string(argv0);
}

static bool spawn_peer(const std::string& self, PosixChildProc& out_proc)
{
    int in_pipe[2] = {-1, -1};
    int out_pipe[2] = {-1, -1};

    if (pipe(in_pipe) != 0) {
        return false;
    }
    if (pipe(out_pipe) != 0) {
        close(in_pipe[0]);
        close(in_pipe[1]);
        return false;
    }

    pid_t pid = fork();
    if (pid < 0) {
        close(in_pipe[0]);
        close(in_pipe[1]);
        close(out_pipe[0]);
        close(out_pipe[1]);
        return false;
    }

    if (pid == 0) {
        dup2(in_pipe[0], STDIN_FILENO);
        dup2(out_pipe[1], STDOUT_FILENO);

        int nul = open("/dev/null", O_WRONLY);
        if (nul >= 0) {
            dup2(nul, STDERR_FILENO);
            close(nul);
        }

        close(in_pipe[1]);
        close(out_pipe[0]);
        close(in_pipe[0]);
        close(out_pipe[1]);

        execl(self.c_str(), self.c_str(), "--peer", nullptr);
        _exit(127);
    }

    out_proc.pid = pid;
    out_proc.write_fd = PosixScopedFd(in_pipe[1]);
    out_proc.read_fd = PosixScopedFd(out_pipe[0]);

    close(in_pipe[0]);
    close(out_pipe[1]);
    return true;
}

static CaseResult run_case_a(const std::string& self)
{
    CaseResult r{"case a", false, -1, -1, false, false,
                 "not run", "POSIX blocking read() + fd close/kill"};

    PosixChildProc proc;
    if (!spawn_peer(self, proc)) {
        r.note = "spawn failed";
        return r;
    }

    std::atomic<bool> reader_done{false};
    int read_fd = proc.read_fd.release();

    std::thread reader([&]() {
        std::array<char, 256> buf;
        while (read(read_fd, buf.data(), buf.size()) > 0) {
            // intentionally ignoring payload
        }
        reader_done.store(true, std::memory_order_release);
        if (read_fd >= 0) {
            close(read_fd);
        }
    });

    std::this_thread::sleep_for(std::chrono::milliseconds(150));

    auto start = clock_t::now();
    int input_fd = proc.write_fd.release();
    if (input_fd >= 0) {
        close(input_fd);
    }
    close(read_fd);
    r.child_reaped = terminate_and_wait_child(proc.pid, 1800, r.child_exit_code);

    bool unblocked = wait_done(reader_done, 2000);
    r.unblocked_ms = since_ms(start);
    r.timed_out = !unblocked;
    r.pass = r.child_reaped && unblocked;
    r.note = unblocked ? "reader returned after transport close / peer death" : "reader remained blocked";

    if (reader.joinable()) {
        reader.join();
    }
    return r;
}

static CaseResult run_case_b(const std::string& self)
{
    CaseResult r{"case b", false, -1, -1, false, false,
                 "not run", "POSIX blocking write() + fd close/kill"};

    PosixChildProc proc;
    if (!spawn_peer(self, proc)) {
        r.note = "spawn failed";
        return r;
    }

    std::atomic<bool> writer_done{false};
    int write_fd = proc.write_fd.release();

    std::thread writer([&]() {
        std::array<char, 1> byte{'X'};
        while (true) {
            ssize_t n = write(write_fd, byte.data(), byte.size());
            if (n > 0) {
                continue;
            }
            if (n < 0 && errno == EINTR) {
                continue;
            }
            break;
        }
        writer_done.store(true, std::memory_order_release);
        if (write_fd >= 0) {
            close(write_fd);
        }
    });

    std::this_thread::sleep_for(std::chrono::milliseconds(150));

    auto start = clock_t::now();
    close(proc.read_fd.release());
    if (write_fd >= 0) {
        close(write_fd);
    }
    r.child_reaped = terminate_and_wait_child(proc.pid, 1800, r.child_exit_code);

    bool unblocked = wait_done(writer_done, 2000);
    r.unblocked_ms = since_ms(start);
    r.timed_out = !unblocked;
    r.pass = r.child_reaped && unblocked;
    r.note = unblocked ? "writer returned after fd close / peer death" : "writer remained blocked";

    if (writer.joinable()) {
        writer.join();
    }
    return r;
}

static CaseResult run_case_c(const std::string& self)
{
    CaseResult r{"case c", false, -1, -1, false, false,
                 "not run", "writer queue + stop frame pending + fd close/kill"};

    PosixChildProc proc;
    if (!spawn_peer(self, proc)) {
        r.note = "spawn failed";
        return r;
    }

    std::atomic<bool> writer_done{false};
    int write_fd = proc.write_fd.release();

    std::mutex queue_m;
    std::condition_variable queue_cv;
    std::deque<std::vector<char>> queue;
    queue.emplace_back(1024 * 1024, 'X');
    queue.emplace_back(std::vector<char>{'s', 't', 'o', 'p'});

    size_t queue_size_before_teardown = 0;

    std::thread writer([&]() {
        while (true) {
            std::vector<char> frame;
            {
                std::unique_lock lk(queue_m);
                queue_cv.wait(lk, [&] { return !queue.empty(); });
                if (queue.empty()) {
                    continue;
                }
                frame = std::move(queue.front());
                queue.pop_front();
            }

            size_t offset = 0;
            while (offset < frame.size()) {
                ssize_t n = write(write_fd, frame.data() + offset, frame.size() - offset);
                if (n > 0) {
                    offset += static_cast<size_t>(n);
                    continue;
                }
                if (n < 0 && errno == EINTR) {
                    continue;
                }
                break;
            }

            std::unique_lock lk(queue_m);
            if (queue.empty()) {
                break;
            }
        }
        writer_done.store(true, std::memory_order_release);
        if (write_fd >= 0) {
            close(write_fd);
        }
    });

    std::this_thread::sleep_for(std::chrono::milliseconds(150));

    {
        std::lock_guard lk(queue_m);
        queue_size_before_teardown = queue.size();
    }

    auto start = clock_t::now();
    close(proc.read_fd.release());
    if (write_fd >= 0) {
        close(write_fd);
    }

    r.child_reaped = terminate_and_wait_child(proc.pid, 1800, r.child_exit_code);

    bool unblocked = wait_done(writer_done, 2000);
    r.unblocked_ms = since_ms(start);
    r.timed_out = !unblocked;

    if (queue_size_before_teardown > 0) {
        r.note = "stop frame remained queued during teardown";
    } else {
        r.note = "stop frame missing before teardown";
    }

    if (unblocked && queue_size_before_teardown > 0 && r.child_reaped) {
        r.pass = true;
    } else {
        r.pass = false;
        if (r.timed_out) {
            r.note += ", writer timeout (teardown depends on stop/flush)";
        }
    }

    if (writer.joinable()) {
        writer.join();
    }
    return r;
}

std::vector<CaseResult> run_all_posix(const std::string& self)
{
    std::vector<CaseResult> results;
    results.push_back(run_case_a(self));
    results.push_back(run_case_b(self));
    results.push_back(run_case_c(self));
    return results;
}

#endif

} // namespace

int main(int argc, char** argv)
{
#ifndef _WIN32
    if (argc > 1 && std::string_view(argv[1]) == "--peer") {
        std::signal(SIGTERM, [](int) { std::_Exit(0); });
        std::signal(SIGINT, [](int) { std::_Exit(0); });
        std::signal(SIGPIPE, SIG_IGN);
        while (true) {
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
    }

    std::signal(SIGPIPE, SIG_IGN);

    const std::string self = executable_path(argv[0]);
    const auto cases = run_all_posix(self);

    for (const auto& r : cases) {
        print_case("POSIX", r);
    }

    write_decision_file(self, cases);

    bool all_pass = true;
    for (const auto& r : cases) {
        if (!r.pass) {
            all_pass = false;
            break;
        }
    }

    if (!all_pass) {
        std::cout << "[POSIX] FAIL: one or more cases did not satisfy timeout/death cleanup criteria.\n";
    }
    return all_pass ? 0 : 1;
#else
    if (argc > 1 && std::string_view(argv[1]) == "--peer") {
        return run_peer_mode_windows();
    }

    const std::string self = executable_path_windows();
    const auto cases = run_all_windows();
    for (const auto& r : cases) {
        print_case("WIN", r);
    }
    write_decision_file(self, {}, cases);

    bool all_pass = true;
    for (const auto& r : cases) {
        if (!r.pass) {
            all_pass = false;
            break;
        }
    }

    if (!all_pass) {
        std::cout << "[WIN] FAIL: one or more cases did not satisfy timeout/death cleanup criteria.\n";
    }
    return all_pass ? 0 : 1;
#endif
}
