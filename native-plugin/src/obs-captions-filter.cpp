// SPDX-License-Identifier: GPL-2.0-or-later
#include "obs-captions-filter.h"
#include "caption-output.h"

#include <chrono>

static void obs_captions_filter_worker(obs_captions_filter_data *filter)
{
	blog(LOG_DEBUG, "obs-captions: worker loop started");

	while (filter->running.load(std::memory_order_acquire)) {
		std::unique_lock<std::mutex> lock(filter->buf_mutex);
		filter->cv.wait(
			lock, [filter] { return !filter->running.load(std::memory_order_acquire); });
		// TODO(ipc): when PCM consumption is added, expand predicate to
		// data-available OR !running.

		if (!filter->running.load(std::memory_order_acquire)) {
			break;
		}

		lock.unlock();

		// TODO(ipc): send PCM to python sidecar, recv caption
		// 현재는 데모 처리 없음.
		blog(LOG_DEBUG, "obs-captions: worker awakened (PCM consume stub)");

		std::this_thread::sleep_for(std::chrono::milliseconds(1));
	}

	blog(LOG_DEBUG, "obs-captions: worker loop exiting");
}

const char *obs_captions_filter_get_name(void *type_data)
{
	(void)type_data;
	return obs_module_text("ObsCaptionsFilter");
}

void *obs_captions_filter_create(obs_data_t *settings, obs_source_t *context)
{
	auto *filter = new obs_captions_filter_data();
	filter->context = context;
	filter->channels = 0;
	filter->sample_rate = 0;
	filter->resampler_to_whisper = nullptr;

	for (size_t i = 0; i < OBS_CAPTIONS_MAX_CHANNELS; i++) {
		deque_init(&filter->input_buffers[i]);
	}
	deque_init(&filter->info_buffer);
	filter->running.store(true, std::memory_order_release);

	if (settings != nullptr) {
		obs_captions_filter_update(filter, settings);
	}

	filter->worker = std::thread(obs_captions_filter_worker, filter);
	return filter;
}

void obs_captions_filter_destroy(void *data)
{
	auto *filter = static_cast<obs_captions_filter_data *>(data);
	if (!filter) {
		return;
	}

	{
		std::lock_guard<std::mutex> lock(filter->buf_mutex);
		filter->running.store(false, std::memory_order_release);
	}
	filter->cv.notify_one();
	if (filter->worker.joinable()) {
		filter->worker.join();
	}

	for (size_t i = 0; i < OBS_CAPTIONS_MAX_CHANNELS; i++) {
		deque_free(&filter->input_buffers[i]);
	}
	deque_free(&filter->info_buffer);

	if (filter->resampler_to_whisper) {
		audio_resampler_destroy(filter->resampler_to_whisper);
		filter->resampler_to_whisper = nullptr;
	}

	delete filter;
}

void obs_captions_filter_update(void *data, obs_data_t *settings)
{
	auto *filter = static_cast<obs_captions_filter_data *>(data);
	if (!filter || !settings) {
		return;
	}

	// TODO(gui): expose engine/line-count/style properties
	// TODO: populate filter->channels from source audio config
	const char *target = obs_data_get_string(settings, "target_text_source");
	filter->target_text_source_name = target != nullptr ? target : "";
}

void obs_captions_filter_get_defaults(obs_data_t *settings)
{
	if (!settings) {
		return;
	}

	obs_data_set_default_string(settings, "target_text_source", "");
	// TODO(gui): defaults for engine, line limits, and style options.
}

obs_properties_t *obs_captions_filter_get_properties(void *data)
{
	(void)data;

	obs_properties_t *props = obs_properties_create();
	obs_properties_add_text(props, "target_text_source", "Target text source", OBS_TEXT_DEFAULT);
	// TODO(gui): add engine selector, line count, style options.
	return props;
}

struct obs_audio_data *obs_captions_filter_filter_audio(void *data, struct obs_audio_data *audio)
{
	auto *filter = static_cast<obs_captions_filter_data *>(data);
	if (!filter || !audio || audio->frames == 0) {
		return audio;
	}

	const size_t frame_bytes = static_cast<size_t>(audio->frames) * sizeof(float);

	{
		std::lock_guard<std::mutex> lock(filter->buf_mutex);
		for (size_t c = 0; c < OBS_CAPTIONS_MAX_CHANNELS; c++) {
			if (!audio->data[c]) {
				continue;
			}
			deque_push_back(&filter->input_buffers[c], audio->data[c], frame_bytes);
		}

		const obs_captions_audio_frame_info info{
			audio->frames,
			filter->sample_rate,
		};
		deque_push_back(&filter->info_buffer, &info, sizeof(info));
	}

	filter->cv.notify_one();
	return audio;
}
