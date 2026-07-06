// SPDX-License-Identifier: GPL-2.0
#include "out_queue.hpp"

#include <chrono>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

void assert_true(bool cond, const char *message)
{
	if (!cond) {
		std::cerr << "FAILED: " << message << std::endl;
		std::exit(1);
	}
}

std::vector<std::uint8_t> payload_from_text(const std::string &text)
{
	return std::vector<std::uint8_t>(text.begin(), text.end());
}

int main()
{
	using namespace obs_native_ipc;

	OutQueue queue(4);

	auto heartbeat_waiter = queue.enqueue_heartbeat(payload_from_text("hb-0"));
	assert_true(!heartbeat_waiter->done(), "heartbeat enqueue should not complete immediately");

	queue.enqueue_audio(payload_from_text("audio-1"));
	queue.enqueue_audio(payload_from_text("audio-2"));
	auto start_waiter_1 = queue.enqueue_control(ControlCommand::Start, 1u, payload_from_text("start-1"));
	auto start_waiter_2 = queue.enqueue_control(ControlCommand::Start, 2u, payload_from_text("start-2"));
	auto start_waiter_3 = queue.enqueue_control(ControlCommand::Start, 3u, payload_from_text("start-3"));
	assert_true(start_waiter_2->done(), "superseded start waiter should complete");
	assert_true(start_waiter_2->reason() == WaitReason::Superseded, "superseded reason");

	std::vector<OutFrame> drained_frames;
	OutFrame first{};
	while (queue.pop_next(first)) {
		drained_frames.push_back(first);
	}

	assert_true(drained_frames.size() == 4u, "expected four pre-stop frames");
	int heartbeat_count = 0;
	int audio_count = 0;
	int control_count = 0;
	bool has_latest_start = false;
	bool has_heartbeat_payload = false;
	for (const auto &f : drained_frames) {
		if (f.kind == FrameKind::Audio) {
			++audio_count;
			const bool has_audio_payload = f.payload == payload_from_text("audio-1") ||
						      f.payload == payload_from_text("audio-2");
			assert_true(has_audio_payload, "audio payload must be complete");
		} else if (f.kind == FrameKind::Heartbeat) {
			++heartbeat_count;
			has_heartbeat_payload = (f.payload == payload_from_text("hb-0"));
		} else if (f.kind == FrameKind::Control) {
			++control_count;
			has_latest_start = (f.control == ControlCommand::Start && f.payload == payload_from_text("start-3"));
		}
	}
	assert_true(audio_count == 2, "expected two audio frames");
	assert_true(heartbeat_count == 1, "expected one heartbeat frame");
	assert_true(control_count == 1, "expected one control frame");
	assert_true(has_latest_start, "latest start control should be transmitted");
	assert_true(has_heartbeat_payload, "heartbeat payload should be complete");

	OutQueue in_flight_guard_queue(4);
	auto guarded_waiter_1 = in_flight_guard_queue.enqueue_control(ControlCommand::Start, 1u,
								    payload_from_text("guarded-start-1"));
	OutFrame guarded_frame{};
	assert_true(in_flight_guard_queue.pop_next(guarded_frame), "first guarded control should pop");
	assert_true(guarded_frame.kind == FrameKind::Control && guarded_frame.control == ControlCommand::Start &&
			   guarded_frame.seq == 1u,
			   "guarded pop should be first control seq");
	assert_true(in_flight_guard_queue.has_in_flight(ControlCommand::Start), "first guarded control should be in-flight");

	auto guarded_waiter_2 = in_flight_guard_queue.enqueue_control(ControlCommand::Start, 2u,
								    payload_from_text("guarded-start-2"));
	assert_true(!guarded_waiter_2->done(), "second same-kind control should be kept as queued");
	assert_true(!in_flight_guard_queue.pop_next(guarded_frame),
		    "second same-kind control should not dispatch while first outstanding");

	assert_true(in_flight_guard_queue.timeout(ControlCommand::Start, 1u), "first guarded control should timeout");
	assert_true(guarded_waiter_1->reason() == WaitReason::Timeout, "first guarded waiter should complete once");
	assert_true(!in_flight_guard_queue.notify(ControlCommand::Start, 1u, WaitReason::Complete),
		    "late notify for timed-out seq should not complete again");

	assert_true(in_flight_guard_queue.pop_next(guarded_frame), "second guarded control should dispatch after first done");
	assert_true(guarded_frame.kind == FrameKind::Control && guarded_frame.seq == 2u,
		    "second guarded control should carry updated seq");
	assert_true(in_flight_guard_queue.notify(ControlCommand::Start, 2u, WaitReason::Complete),
		    "second guarded control should complete normally");
	assert_true(guarded_waiter_2->reason() == WaitReason::Complete, "second guarded waiter should complete");

	auto stop_waiter = queue.enqueue_control(ControlCommand::Stop, 100u, payload_from_text("stop"));
	assert_true(!stop_waiter->done(), "new control should be pending");
	assert_true(queue.has_queued(ControlCommand::Stop), "stop must be queued");

	OutFrame stop_frame{};
	assert_true(queue.pop_next(stop_frame), "stop frame should pop");
	assert_true(stop_frame.control == ControlCommand::Stop, "popped frame should be stop");
	assert_true(stop_frame.seq == 100u, "stop seq should match");
	assert_true(queue.has_in_flight(ControlCommand::Stop), "stop should be in-flight after pop");
	assert_true(stop_waiter->reason() == WaitReason::None, "waiter not yet completed");

	assert_true(queue.notify(ControlCommand::Stop, 100u, WaitReason::Timeout), "timeout should complete");
	assert_true(stop_waiter->reason() == WaitReason::Timeout, "waiter reason should be timeout first");
	assert_true(!queue.notify(ControlCommand::Stop, 100u, WaitReason::Complete),
		    "duplicate completion should not run");
	assert_true(stop_waiter->reason() == WaitReason::Timeout, "waiter should keep first completion reason");

	auto stop_waiter_2 = queue.enqueue_control(ControlCommand::Stop, 101u, payload_from_text("stop-2"));
	auto heartbeat_after = queue.enqueue_heartbeat(payload_from_text("hb-1"));
	assert_true(stop_waiter_2->reason() == WaitReason::None, "new waiter should be pending");
	assert_true(heartbeat_after->reason() == WaitReason::None, "heartbeat after queue should be pending");

	queue.begin_teardown();
	assert_true(stop_waiter_2->done() &&
			   (stop_waiter_2->reason() == WaitReason::Cancelled || stop_waiter_2->reason() == WaitReason::NoSession),
		    "waiting waiter should complete on teardown");
	assert_true(!heartbeat_after->done() || heartbeat_after->reason() == WaitReason::NoSession,
		    "heartbeat should complete on teardown");

	auto late_waiter = queue.enqueue_control(ControlCommand::Reconfigure, 200u, payload_from_text("late"));
	assert_true(late_waiter->done(), "post-teardown enqueue should be completed");
	assert_true(late_waiter->reason() == WaitReason::NoSession, "post-teardown reason should be no-session");

	OutFrame drained{};
	assert_true(!queue.pop_next(drained), "no additional frame should be drainable after teardown");

	queue.begin_teardown();
	assert_true(!queue.accepting(), "accepting should remain false");

	std::cout << "writer_serialize_test: PASS" << std::endl;
	return 0;
}
