// SPDX-License-Identifier: GPL-2.0
#include "epoch_gate.hpp"

#include <iostream>

void assert_true(bool cond, const char *message)
{
	if (!cond) {
		std::cerr << "FAILED: " << message << std::endl;
		std::exit(1);
	}
}

int main()
{
	using namespace obs_native_ipc;

	EpochGate gate(1);
	assert_true(gate.should_apply(1), "active epoch should apply");
	assert_true(!gate.should_apply(2), "stale epoch should reject");

	CaptionEvent evt1{1u, 10u, 1u, "hello", true};
	assert_true(gate.evaluate(evt1) == CaptionDecision::Accept, "first final should accept");
	assert_true(gate.evaluate(evt1) == CaptionDecision::DropDuplicate, "same final ts/text should dedupe");

	CaptionEvent evt2{1u, 9u, 1u, "early", true};
	assert_true(gate.evaluate(evt2) == CaptionDecision::DropOutOfOrder, "earlier seq should drop");

	CaptionEvent evt3{1u, 11u, 1u, "next", false};
	assert_true(gate.evaluate(evt3) == CaptionDecision::Accept, "higher seq should accept");

	gate.advance_epoch();
	assert_true(gate.should_apply(1u) == false, "old epoch should be stale after advance");
	assert_true(gate.should_apply(2u), "new epoch should apply after advance");

	CaptionEvent evt4{1u, 999u, 1u, "stale", true};
	assert_true(gate.evaluate(evt4) == CaptionDecision::DropStaleEpoch, "stale epoch event should drop");
	CaptionEvent evt5{2u, 1u, 2u, "new low", true};
	assert_true(gate.evaluate(evt5) == CaptionDecision::Accept, "new epoch should accept lower seq");

	CaptionEvent evt6{2u, 1u, 2u, "new low", true};
	assert_true(gate.evaluate(evt6) == CaptionDecision::DropDuplicate, "duplicate seq+ts+text should drop only in new epoch");

	std::cout << "epoch_gate_test: PASS" << std::endl;
	return 0;
}
