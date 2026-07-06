// SPDX-License-Identifier: GPL-2.0
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <memory>
#include <mutex>
#include <vector>

namespace obs_native_ipc {

enum class ControlCommand : std::uint16_t {
	Start = 1,
	Stop = 2,
	Flush = 3,
	Reconfigure = 4,
	Heartbeat = 8,
};

enum class FrameKind {
	Audio,
	Control,
	Heartbeat,
};

enum class WaitReason {
	None,
	Complete,
	Timeout,
	Cancelled,
	Superseded,
	NoSession,
};

class Waiter {
public:
	Waiter() = default;

	bool complete(WaitReason reason);
	bool done() const;
	WaitReason reason() const;

private:
	mutable std::mutex mutex_;
	WaitReason result_{WaitReason::None};
};

struct OutFrame {
	FrameKind kind{FrameKind::Audio};
	ControlCommand control{ControlCommand::Heartbeat};
	std::uint64_t seq{0};
	std::vector<std::uint8_t> payload;
	std::shared_ptr<Waiter> waiter;
};

class OutQueue {
public:
	explicit OutQueue(std::size_t audio_capacity = 16);

	std::shared_ptr<Waiter> enqueue_control(ControlCommand command,
					       std::uint64_t seq,
					       std::vector<std::uint8_t> payload);
	std::shared_ptr<Waiter> enqueue_heartbeat(std::vector<std::uint8_t> payload);
	void enqueue_audio(std::vector<std::uint8_t> payload);

	bool pop_next(OutFrame &frame);
	bool notify(ControlCommand command, std::uint64_t seq, WaitReason reason);
	bool timeout(ControlCommand command, std::uint64_t seq);
	bool cancel(ControlCommand command, std::uint64_t seq);
	void begin_teardown();
	bool accepting() const;

	bool has_in_flight(ControlCommand command) const;
	bool has_queued(ControlCommand command) const;
	std::size_t queued_audio() const;
	std::size_t dropped_audio() const;

private:
	static constexpr std::size_t kCommandCount = 5;

	struct CommandState {
		std::shared_ptr<Waiter> in_flight_waiter;
		std::uint64_t in_flight_seq = 0;
		std::vector<std::uint8_t> in_flight_payload;
		bool in_flight = false;

		std::shared_ptr<Waiter> queued_waiter;
		std::uint64_t queued_seq = 0;
		std::vector<std::uint8_t> queued_payload;
		bool has_queued = false;

		bool queued_in_schedule = false;
	};

	static std::size_t command_index(ControlCommand command);
	void cancel_waiter(std::shared_ptr<Waiter> waiter, WaitReason reason);
	void complete_queued(CommandState &state, WaitReason reason);
	void remove_stale_controls();

	mutable std::mutex mutex_;
	std::array<CommandState, kCommandCount> commands_{};
	std::deque<ControlCommand> scheduled_controls_;
	std::deque<std::vector<std::uint8_t>> audio_queue_;
	std::size_t audio_capacity_;
	std::size_t dropped_audio_{0};
	bool accepting_{true};
};

} // namespace obs_native_ipc
