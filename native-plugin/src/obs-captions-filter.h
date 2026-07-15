// SPDX-License-Identifier: GPL-2.0-or-later
#ifndef OBS_CAPTIONS_FILTER_H
#define OBS_CAPTIONS_FILTER_H

#include <media-io/audio-resampler.h>
#include <obs-module.h>

#ifdef __cplusplus
#include "ipc-bridge.hpp"

#include <memory>
#include <mutex>
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

struct obs_captions_filter_data {
	obs_source_t *context;
	size_t channels;
	uint32_t sample_rate;
	uint32_t resampler_sample_rate;
	audio_resampler_t *resampler_to_16k;
#ifdef __cplusplus
	std::unique_ptr<obs_native_ipc::IpcBridge> bridge;
	std::mutex settings_mutex;
	std::string target_text_source_name;
	std::string config_path;
	std::string sidecar_exe;
#endif
};

#ifdef __cplusplus
}
#endif

#endif // OBS_CAPTIONS_FILTER_H
