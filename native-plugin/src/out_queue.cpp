// SPDX-License-Identifier: GPL-2.0
#include "out_queue.hpp"

#include <algorithm>

namespace obs_native_ipc {

namespace {

std::size_t command_to_index(ControlCommand command)
{
	switch (command) {
	case ControlCommand::Start:
		return 0;
	case ControlCommand::Stop:
		return 1;
	case ControlCommand::Flush:
		return 2;
	case ControlCommand::Reconfigure:
		return 3;
	case ControlCommand::Heartbeat:
		return 4;
	default:
		return 4;
	}
}

bool expects_reply(ControlCommand command)
{
	return command != ControlCommand::Heartbeat;
}

} // namespace

bool Waiter::complete(WaitReason reason)
{
	std::lock_guard<std::mutex> lock(mutex_);
	if (result_ != WaitReason::None) {
		return false;
	}
	result_ = reason;
	return true;
}

bool Waiter::done() const
{
	std::lock_guard<std::mutex> lock(mutex_);
	return result_ != WaitReason::None;
}

WaitReason Waiter::reason() const
{
	std::lock_guard<std::mutex> lock(mutex_);
	return result_;
}

std::size_t OutQueue::command_index(ControlCommand command)
{
	return command_to_index(command);
}

OutQueue::OutQueue(std::size_t audio_capacity) : audio_capacity_(audio_capacity) {}

void OutQueue::cancel_waiter(std::shared_ptr<Waiter> waiter, WaitReason reason)
{
	if (waiter) {
		waiter->complete(reason);
	}
}

void OutQueue::complete_queued(CommandState &state, WaitReason reason)
{
	if (state.has_queued) {
		cancel_waiter(state.queued_waiter, reason);
		state.queued_waiter.reset();
		state.queued_seq = 0;
		state.queued_payload.clear();
		state.has_queued = false;
	}
}

void OutQueue::remove_stale_controls()
{
	while (!scheduled_controls_.empty()) {
		const auto cmd = scheduled_controls_.front();
		const auto idx = command_index(cmd);
		if (!commands_[idx].has_queued) {
			scheduled_controls_.pop_front();
			commands_[idx].queued_in_schedule = false;
			continue;
		}
		break;
	}
}

std::shared_ptr<Waiter> OutQueue::enqueue_control(ControlCommand command, std::uint64_t seq,
						  std::vector<std::uint8_t> payload)
{
	auto waiter = std::make_shared<Waiter>();
	std::lock_guard<std::mutex> lock(mutex_);
	if (!accepting_) {
		waiter->complete(WaitReason::NoSession);
		return waiter;
	}

	auto &state = commands_[command_index(command)];
	if (state.has_queued) {
		state.queued_waiter->complete(WaitReason::Superseded);
	}

	state.has_queued = true;
	state.queued_seq = seq;
	state.queued_payload = std::move(payload);
	state.queued_waiter = waiter;

	if (!state.queued_in_schedule && !state.in_flight) {
		scheduled_controls_.push_back(command);
		state.queued_in_schedule = true;
	}

	return waiter;
}

std::shared_ptr<Waiter> OutQueue::enqueue_heartbeat(std::vector<std::uint8_t> payload)
{
	return enqueue_control(ControlCommand::Heartbeat, 0, std::move(payload));
}

void OutQueue::enqueue_audio(std::vector<std::uint8_t> payload)
{
	std::lock_guard<std::mutex> lock(mutex_);
	if (audio_queue_.size() >= audio_capacity_) {
		audio_queue_.pop_front();
		++dropped_audio_;
	}
	audio_queue_.push_back(std::move(payload));
}

bool OutQueue::pop_next(OutFrame &frame)
{
	std::lock_guard<std::mutex> lock(mutex_);
	remove_stale_controls();

	if (!scheduled_controls_.empty()) {
		const auto command = scheduled_controls_.front();
		scheduled_controls_.pop_front();

		auto &state = commands_[command_index(command)];
		state.queued_in_schedule = false;

		if (state.has_queued) {
			const bool reply_expected = expects_reply(command);
			frame.kind = (command == ControlCommand::Heartbeat ? FrameKind::Heartbeat : FrameKind::Control);
			frame.control = command;
			frame.seq = state.queued_seq;
			frame.payload = state.queued_payload;
			frame.waiter = state.queued_waiter;

			state.in_flight = reply_expected;
			state.in_flight_seq = state.queued_seq;
			state.in_flight_waiter = state.queued_waiter;
			state.in_flight_payload = std::move(state.queued_payload);

			state.queued_waiter.reset();
			state.queued_seq = 0;
			state.has_queued = false;

			if (!reply_expected) {
				state.in_flight_waiter.reset();
				state.in_flight = false;
				state.in_flight_seq = 0;
				state.in_flight_payload.clear();
			}
			return true;
		}
	}

	if (!audio_queue_.empty()) {
		frame.kind = FrameKind::Audio;
		frame.control = ControlCommand::Heartbeat;
		frame.seq = 0;
		frame.payload = std::move(audio_queue_.front());
		audio_queue_.pop_front();
		frame.waiter.reset();
		return true;
	}

	return false;
}

bool OutQueue::notify(ControlCommand command, std::uint64_t seq, WaitReason reason)
{
	std::lock_guard<std::mutex> lock(mutex_);
	auto &state = commands_[command_index(command)];
	if (!state.in_flight) {
		return false;
	}
	if (state.in_flight_seq != seq) {
		return false;
	}

	cancel_waiter(state.in_flight_waiter, reason);
	state.in_flight_waiter.reset();
	state.in_flight_seq = 0;
	state.in_flight = false;
	state.in_flight_payload.clear();
	if (state.has_queued && !state.queued_in_schedule) {
		scheduled_controls_.push_back(command);
		state.queued_in_schedule = true;
	}
	return true;
}

bool OutQueue::timeout(ControlCommand command, std::uint64_t seq)
{
	return notify(command, seq, WaitReason::Timeout);
}

bool OutQueue::cancel(ControlCommand command, std::uint64_t seq)
{
	return notify(command, seq, WaitReason::Cancelled);
}

void OutQueue::begin_teardown()
{
	std::lock_guard<std::mutex> lock(mutex_);
	accepting_ = false;
	for (auto &state : commands_) {
		if (state.in_flight) {
			if (state.in_flight_waiter) {
				state.in_flight_waiter->complete(WaitReason::Cancelled);
			}
			state.in_flight = false;
			state.in_flight_waiter.reset();
			state.in_flight_seq = 0;
			state.in_flight_payload.clear();
		}
		complete_queued(state, WaitReason::NoSession);
		state.queued_in_schedule = false;
	}
	scheduled_controls_.clear();
}

bool OutQueue::accepting() const
{
	std::lock_guard<std::mutex> lock(mutex_);
	return accepting_;
}

bool OutQueue::has_in_flight(ControlCommand command) const
{
	std::lock_guard<std::mutex> lock(mutex_);
	return commands_[command_index(command)].in_flight;
}

bool OutQueue::has_queued(ControlCommand command) const
{
	std::lock_guard<std::mutex> lock(mutex_);
	return commands_[command_index(command)].has_queued;
}

std::size_t OutQueue::queued_audio() const
{
	std::lock_guard<std::mutex> lock(mutex_);
	return audio_queue_.size();
}

std::size_t OutQueue::dropped_audio() const
{
	std::lock_guard<std::mutex> lock(mutex_);
	return dropped_audio_;
}

} // namespace obs_native_ipc
