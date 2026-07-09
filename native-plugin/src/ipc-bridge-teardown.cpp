// SPDX-License-Identifier: GPL-2.0-or-later
#include "ipc-bridge.hpp"

#include <chrono>
#include <iostream>
#include <new>
#include <thread>

namespace obs_native_ipc {

void IpcBridge::latch_desired(Desired intent)
{
	if (intent == Desired::Destroy) {
		no_respawn_.store(true, std::memory_order_release);
		stop_requested_.store(true, std::memory_order_release);
	}
	auto wanted = static_cast<std::uint8_t>(intent);
	auto cur = desired_.load(std::memory_order_acquire);
	while (cur < wanted && !desired_.compare_exchange_weak(cur, wanted, std::memory_order_acq_rel)) {
	}
}

IpcBridge::Desired IpcBridge::desired() const
{
	return static_cast<Desired>(desired_.load(std::memory_order_acquire));
}

void IpcBridge::request_destroy()
{
	latch_desired(Desired::Destroy);
	running_.store(false, std::memory_order_release);
	transport_.cancel();
	state_cv_.notify_all();
	ready_cv_.notify_all();
}

void IpcBridge::advance_epoch_for_teardown()
{
	std::lock_guard lock(epoch_mutex_);
	epoch_gate_.advance_epoch();
	session_epoch_.store(epoch_gate_.active_epoch(), std::memory_order_release);
}

void IpcBridge::set_ready_state(bool ready)
{
	{
		std::lock_guard lock(state_mutex_);
		session_ready_.store(ready, std::memory_order_release);
	}
	ready_cv_.notify_all();
}

void IpcBridge::mark_degraded()
{
	no_respawn_.store(true, std::memory_order_release);
	terminal_state_.store(static_cast<std::uint8_t>(TerminalState::Degraded), std::memory_order_release);
	running_.store(false, std::memory_order_release);
	stop_requested_.store(true, std::memory_order_release);
	std::cerr << "obs-captions ipc bridge degraded terminal\n";
}

IpcBridge::Desired IpcBridge::execute_teardown()
{
	bool expected_owner = false;
	if (!teardown_owner_.compare_exchange_strong(expected_owner, true, std::memory_order_acq_rel)) {
		return desired();
	}

	teardown_count_.fetch_add(1u, std::memory_order_acq_rel);
	advance_epoch_for_teardown();
	set_ready_state(false);
	session_running_.store(false, std::memory_order_release);
	control_queue_.begin_teardown();

	transport_.cancel();
	(void)transport_.reap();
	join_threads(config_.stop_timeout);

	const auto final_desired = desired();
	if (final_desired == Desired::Destroy || no_respawn_.load(std::memory_order_acquire)) {
		const bool already_degraded = terminal_state_.load(std::memory_order_acquire) ==
					      static_cast<std::uint8_t>(TerminalState::Degraded);
		if (!already_degraded) {
			auto quiesce = quiesce_snapshot();
			const bool drained = !quiesce || quiesce->begin_quiesce_and_wait(config_.stop_timeout);
			if (drained) {
				release_audio_ring();
				terminal_state_.store(static_cast<std::uint8_t>(TerminalState::Inactive),
						      std::memory_order_release);
			} else {
				mark_degraded();
			}
			clear_control_queue();
		}
		desired_.store(static_cast<std::uint8_t>(Desired::Destroy), std::memory_order_release);
		teardown_owner_.store(false, std::memory_order_release);
		state_cv_.notify_all();
		ready_cv_.notify_all();
		return Desired::Destroy;
	}

	clear_control_queue();

#ifdef OBS_NATIVE_IPC_TESTING
	if (test_pause_before_respawn_.load(std::memory_order_acquire)) {
		{
			std::lock_guard lock(state_mutex_);
			test_paused_before_respawn_.store(true, std::memory_order_release);
		}
		test_cv_.notify_all();
		std::unique_lock lock(state_mutex_);
		test_cv_.wait(lock, [this]() { return test_release_before_respawn_.load(std::memory_order_acquire); });
	}
#endif

	auto expected_desired = static_cast<std::uint8_t>(Desired::Restart);
	if (desired_.compare_exchange_strong(expected_desired, static_cast<std::uint8_t>(Desired::Run),
					     std::memory_order_acq_rel)) {
		teardown_owner_.store(false, std::memory_order_release);
		state_cv_.notify_all();
		return Desired::Restart;
	}

	if (desired() == Desired::Destroy || no_respawn_.load(std::memory_order_acquire)) {
		const bool already_degraded = terminal_state_.load(std::memory_order_acquire) ==
					      static_cast<std::uint8_t>(TerminalState::Degraded);
		if (!already_degraded) {
			auto quiesce = quiesce_snapshot();
			const bool drained = !quiesce || quiesce->begin_quiesce_and_wait(config_.stop_timeout);
			if (drained) {
				release_audio_ring();
				terminal_state_.store(static_cast<std::uint8_t>(TerminalState::Inactive),
						      std::memory_order_release);
			} else {
				mark_degraded();
			}
			clear_control_queue();
		}
	}
	teardown_owner_.store(false, std::memory_order_release);
	state_cv_.notify_all();
	ready_cv_.notify_all();
	return desired();
}

std::shared_ptr<SeqlockRing<AudioSlot>> IpcBridge::audio_ring_snapshot() const
{
	std::lock_guard lock(audio_mutex_);
	return audio_ring_;
}

std::shared_ptr<ProducerQuiesce> IpcBridge::quiesce_snapshot() const
{
	std::lock_guard lock(audio_mutex_);
	return quiesce_;
}

void IpcBridge::reset_audio_path(std::size_t capacity)
{
	std::lock_guard lock(audio_mutex_);
	quiesce_ = std::make_shared<ProducerQuiesce>();
	audio_ring_ = std::make_shared<SeqlockRing<AudioSlot>>(capacity ? capacity : 1u);
	ring_released_.store(false, std::memory_order_release);
}

void IpcBridge::release_audio_ring()
{
	std::lock_guard lock(audio_mutex_);
	audio_ring_.reset();
	ring_released_.store(true, std::memory_order_release);
}

void IpcBridge::notify_thread_exit()
{
	state_cv_.notify_all();
}

#ifdef OBS_NATIVE_IPC_TESTING
void IpcBridge::test_request_restart()
{
	request_restart();
}

std::shared_ptr<Waiter> IpcBridge::test_enqueue_control(ControlCommand command)
{
	const auto seq = next_control_seq_.fetch_add(1u, std::memory_order_relaxed);
	return control_queue_.enqueue_control(command, seq, {});
}

std::uint64_t IpcBridge::test_next_control_seq() const
{
	return next_control_seq_.load(std::memory_order_relaxed);
}

bool IpcBridge::test_has_in_flight(ControlCommand command) const
{
	return control_queue_.has_in_flight(command);
}

void IpcBridge::test_timeout_control(ControlCommand command, std::uint64_t seq)
{
	control_queue_.timeout(command, seq);
}

void IpcBridge::test_inject_caption(std::uint32_t reader_epoch, std::uint32_t payload_epoch, std::uint64_t seq,
				    const std::string &text)
{
	DecodedFrame frame;
	frame.type = MessageType::CAPTION_FINAL;
	append_u32(frame.payload, payload_epoch);
	append_u64(frame.payload, 1u);
	append_u64(frame.payload, seq);
	append_u32(frame.payload, static_cast<std::uint32_t>(text.size()));
	frame.payload.insert(frame.payload.end(), text.begin(), text.end());
	process_payload(frame, reader_epoch);
}

void IpcBridge::test_inject_status(std::uint32_t reader_epoch, std::uint16_t code, std::uint64_t seq)
{
	DecodedFrame frame;
	frame.type = MessageType::STATUS;
	append_u16(frame.payload, code);
	append_u64(frame.payload, seq);
	append_u32(frame.payload, 0u);
	process_payload(frame, reader_epoch);
}

void IpcBridge::test_inject_flush_done(std::uint32_t reader_epoch, std::uint64_t seq)
{
	DecodedFrame frame;
	frame.type = MessageType::FLUSH_DONE;
	append_u64(frame.payload, seq);
	process_payload(frame, reader_epoch);
}

std::size_t IpcBridge::test_teardown_count() const
{
	return teardown_count_.load(std::memory_order_acquire);
}

std::uint8_t IpcBridge::test_terminal_state() const
{
	return terminal_state_.load(std::memory_order_acquire);
}

bool IpcBridge::test_session_running() const
{
	return session_running_.load(std::memory_order_acquire);
}

bool IpcBridge::test_ring_released() const
{
	return ring_released_.load(std::memory_order_acquire);
}

std::size_t IpcBridge::test_ring_capacity() const
{
	auto ring = audio_ring_snapshot();
	return ring ? ring->capacity() : 0u;
}

std::size_t IpcBridge::test_control_queue_reset_count() const
{
	return test_control_queue_reset_count_.load(std::memory_order_acquire);
}

void IpcBridge::test_pause_before_respawn(bool enabled)
{
	test_pause_before_respawn_.store(enabled, std::memory_order_release);
	test_paused_before_respawn_.store(false, std::memory_order_release);
	test_release_before_respawn_.store(false, std::memory_order_release);
}

bool IpcBridge::test_wait_before_respawn(std::chrono::milliseconds timeout)
{
	std::unique_lock lock(state_mutex_);
	return test_cv_.wait_for(lock, timeout, [this]() {
		return test_paused_before_respawn_.load(std::memory_order_acquire);
	});
}

void IpcBridge::test_release_before_respawn()
{
	test_release_before_respawn_.store(true, std::memory_order_release);
	test_cv_.notify_all();
}

void IpcBridge::test_pause_after_producer_enter(bool enabled)
{
	test_pause_after_producer_enter_.store(enabled, std::memory_order_release);
	test_paused_after_producer_enter_.store(false, std::memory_order_release);
	test_release_after_producer_enter_.store(false, std::memory_order_release);
}

bool IpcBridge::test_wait_after_producer_enter(std::chrono::milliseconds timeout)
{
	std::unique_lock lock(state_mutex_);
	return test_cv_.wait_for(lock, timeout, [this]() {
		return test_paused_after_producer_enter_.load(std::memory_order_acquire);
	});
}

void IpcBridge::test_release_after_producer_enter()
{
	test_release_after_producer_enter_.store(true, std::memory_order_release);
	test_cv_.notify_all();
}

void IpcBridge::test_pause_writer_before_exit(bool enabled)
{
	test_pause_writer_before_exit_.store(enabled, std::memory_order_release);
	test_paused_writer_before_exit_.store(false, std::memory_order_release);
	test_release_writer_before_exit_.store(false, std::memory_order_release);
}

bool IpcBridge::test_wait_writer_before_exit(std::chrono::milliseconds timeout)
{
	std::unique_lock lock(state_mutex_);
	return test_cv_.wait_for(lock, timeout, [this]() {
		return test_paused_writer_before_exit_.load(std::memory_order_acquire);
	});
}

void IpcBridge::test_release_writer_before_exit()
{
	test_release_writer_before_exit_.store(true, std::memory_order_release);
	test_cv_.notify_all();
}

bool IpcBridge::test_wait_writer_exited(std::chrono::milliseconds timeout)
{
	std::unique_lock lock(state_mutex_);
	return state_cv_.wait_for(lock, timeout, [this]() { return writer_exited_.load(std::memory_order_acquire); });
}
#endif

} // namespace obs_native_ipc
