// SPDX-License-Identifier: GPL-2.0
#include "epoch_gate.hpp"

namespace obs_native_ipc {

EpochGate::EpochGate(std::uint32_t initial_epoch) : active_epoch_(initial_epoch) {}

std::uint32_t EpochGate::active_epoch() const
{
	return active_epoch_.load(std::memory_order_acquire);
}

bool EpochGate::should_apply(std::uint32_t reader_epoch) const
{
	return reader_epoch == active_epoch_.load(std::memory_order_acquire);
}

void EpochGate::advance_epoch()
{
	active_epoch_.fetch_add(1u, std::memory_order_release);
	has_last_ = false;
	last_seq_ = 0;
	last_timestamp_ = 0;
	last_text_.clear();
}

CaptionDecision EpochGate::evaluate(const CaptionEvent &event)
{
	if (!should_apply(event.epoch)) {
		return CaptionDecision::DropStaleEpoch;
	}

	if (has_last_) {
		if (event.seq < last_seq_) {
			return CaptionDecision::DropOutOfOrder;
		}

		if (event.is_final && event.timestamp_ns == last_timestamp_ && event.text == last_text_) {
			return CaptionDecision::DropDuplicate;
		}
	}

	if (!has_last_ || event.seq > last_seq_ || event.text != last_text_) {
		last_seq_ = event.seq;
		last_timestamp_ = event.timestamp_ns;
		last_text_ = event.text;
		has_last_ = true;
	}

	return CaptionDecision::Accept;
}

} // namespace obs_native_ipc
