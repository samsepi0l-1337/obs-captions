// SPDX-License-Identifier: GPL-2.0-or-later
#ifndef OBS_CAPTIONS_FILTER_H
#define OBS_CAPTIONS_FILTER_H

#include <obs-module.h>
#include <media-io/audio-resampler.h>
#include <util/deque.h>

#ifdef __cplusplus
#include <atomic>
#include <condition_variable>
#include <mutex>
#include <thread>
#include <string>
#endif

#define OBS_CAPTIONS_MAX_CHANNELS 8

#ifdef __cplusplus
extern "C" {
#endif

const char *obs_captions_filter_get_name(void *type_data);
void *obs_captions_filter_create(obs_data_t *settings, obs_source_t *context);
void obs_captions_filter_destroy(void *data);
void obs_captions_filter_update(void *data, obs_data_t *settings);
void obs_captions_filter_get_defaults(obs_data_t *settings);
obs_properties_t *obs_captions_filter_get_properties(void *data);
struct obs_audio_data *obs_captions_filter_filter_audio(void *data, struct obs_audio_data *audio);

struct obs_captions_audio_frame_info {
	uint64_t frames;
	uint32_t sample_rate;
};

struct obs_captions_filter_data {
	obs_source_t *context;
	size_t channels;
	uint32_t sample_rate;
	struct deque info_buffer;
	struct deque input_buffers[OBS_CAPTIONS_MAX_CHANNELS];
	audio_resampler_t *resampler_to_whisper;
#ifdef __cplusplus
	std::thread worker;
	std::mutex buf_mutex;
	std::condition_variable cv;
	std::atomic<bool> running;
	std::string target_text_source_name;
#endif
};

#ifdef __cplusplus
}
#endif

#endif // OBS_CAPTIONS_FILTER_H
