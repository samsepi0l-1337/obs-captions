// SPDX-License-Identifier: GPL-2.0-or-later
#include "obs-captions-filter.h"
#include "caption-output.h"

#include <algorithm>
#include <array>

namespace {

constexpr const char *SETTING_TARGET_TEXT_SOURCE = "target_text_source";
constexpr const char *SETTING_CONFIG_PATH = "config_path";
constexpr const char *SETTING_SIDECAR_EXE = "sidecar_exe";
constexpr uint32_t TARGET_SAMPLE_RATE = 16000u;

std::string obs_data_get_string_value(obs_data_t *settings, const char *name)
{
	const char *value = obs_data_get_string(settings, name);
	return value != nullptr ? value : "";
}

obs_native_ipc::IpcBridge::Config make_bridge_config(const std::string &sidecar_exe,
						     const std::string &config_path)
{
	obs_native_ipc::IpcBridge::Config cfg;
	cfg.spawn.argv = {sidecar_exe, "ipc-sidecar", "--config", config_path};
	cfg.config_path = config_path;
	return cfg;
}

bool start_bridge(obs_captions_filter_data *filter)
{
	if (!filter->bridge) {
		return false;
	}

	std::string sidecar_exe;
	std::string config_path;
	{
		std::lock_guard<std::mutex> lock(filter->settings_mutex);
		sidecar_exe = filter->sidecar_exe;
		config_path = filter->config_path;
	}

	if (sidecar_exe.empty()) {
		blog(LOG_WARNING, "obs-captions: sidecar executable is not configured; audio passes through");
		return false;
	}

	const bool started = filter->bridge->start(make_bridge_config(sidecar_exe, config_path));
	if (!started) {
		blog(LOG_ERROR, "obs-captions: failed to start IPC sidecar '%s'", sidecar_exe.c_str());
	}
	return started;
}

void stop_bridge(obs_captions_filter_data *filter)
{
	if (filter->bridge) {
		filter->bridge->stop();
	}
}

size_t downmix_planar_to_mono(uint8_t *const data[], uint32_t offset, uint32_t frames, float *mono)
{
	if (!data || !mono || frames == 0u) {
		return 0u;
	}

	size_t present_channels = 0u;
	for (size_t c = 0u; c < OBS_CAPTIONS_MAX_CHANNELS; ++c) {
		const auto *channel = reinterpret_cast<const float *>(data[c]);
		if (!channel) {
			continue;
		}

		if (present_channels == 0u) {
			std::copy(channel + offset, channel + offset + frames, mono);
		} else {
			for (uint32_t i = 0u; i < frames; ++i) {
				mono[i] += channel[offset + i];
			}
		}
		++present_channels;
	}

	if (present_channels > 1u) {
		const float scale = 1.0f / static_cast<float>(present_channels);
		for (uint32_t i = 0u; i < frames; ++i) {
			mono[i] *= scale;
		}
	}

	return present_channels;
}

void destroy_resampler(obs_captions_filter_data *filter)
{
	if (filter->resampler_to_16k) {
		audio_resampler_destroy(filter->resampler_to_16k);
		filter->resampler_to_16k = nullptr;
		filter->resampler_sample_rate = 0u;
	}
}

bool ensure_resampler_to_16k(obs_captions_filter_data *filter, uint32_t sample_rate)
{
	if (filter->resampler_to_16k && filter->resampler_sample_rate == sample_rate) {
		return true;
	}

	destroy_resampler(filter);

	struct resample_info src = {};
	src.samples_per_sec = sample_rate;
	src.format = AUDIO_FORMAT_FLOAT;
	src.speakers = SPEAKERS_MONO;

	struct resample_info dst = {};
	dst.samples_per_sec = TARGET_SAMPLE_RATE;
	dst.format = AUDIO_FORMAT_FLOAT;
	dst.speakers = SPEAKERS_MONO;

	filter->resampler_to_16k = audio_resampler_create(&dst, &src);
	if (!filter->resampler_to_16k) {
		blog(LOG_WARNING, "obs-captions: failed to create %u Hz to 16 kHz audio resampler", sample_rate);
		return false;
	}

	filter->resampler_sample_rate = sample_rate;
	return true;
}

void push_resampled_audio(obs_captions_filter_data *filter, const float *mono, uint32_t frames)
{
	const uint8_t *input[1] = {};
	uint8_t *output[1] = {};
	uint32_t out_frames = 0u;
	uint64_t ts_offset = 0u;

	input[0] = reinterpret_cast<const uint8_t *>(mono);
	if (audio_resampler_resample(filter->resampler_to_16k, output, &out_frames, &ts_offset, input, frames) &&
	    output[0] && out_frames > 0u) {
		filter->bridge->push_audio(reinterpret_cast<const float *>(output[0]), out_frames, 1u);
	}
}

} // namespace

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
	filter->resampler_sample_rate = 0;
	filter->resampler_to_16k = nullptr;
	filter->bridge = std::make_unique<obs_native_ipc::IpcBridge>();
	filter->bridge->set_caption_callback([filter](const obs_native_ipc::CaptionEvent &event) {
		std::string target;
		{
			std::lock_guard<std::mutex> lock(filter->settings_mutex);
			target = filter->target_text_source_name;
		}
		if (!target.empty() && !send_caption_to_source(target.c_str(), event.text.c_str())) {
			blog(LOG_DEBUG, "obs-captions: target text source '%s' not available", target.c_str());
		}
	});

	if (settings != nullptr) {
		obs_captions_filter_update(filter, settings);
	}

	return filter;
}

void obs_captions_filter_destroy(void *data)
{
	auto *filter = static_cast<obs_captions_filter_data *>(data);
	if (!filter) {
		return;
	}

	stop_bridge(filter);
	destroy_resampler(filter);
	delete filter;
}

void obs_captions_filter_update(void *data, obs_data_t *settings)
{
	auto *filter = static_cast<obs_captions_filter_data *>(data);
	if (!filter || !settings) {
		return;
	}

	const std::string target = obs_data_get_string_value(settings, SETTING_TARGET_TEXT_SOURCE);
	const std::string config_path = obs_data_get_string_value(settings, SETTING_CONFIG_PATH);
	const std::string sidecar_exe = obs_data_get_string_value(settings, SETTING_SIDECAR_EXE);

	bool restart = false;
	{
		std::lock_guard<std::mutex> lock(filter->settings_mutex);
		restart = filter->config_path != config_path || filter->sidecar_exe != sidecar_exe;
		filter->target_text_source_name = target;
		filter->config_path = config_path;
		filter->sidecar_exe = sidecar_exe;
	}

	if (restart) {
		stop_bridge(filter);
		(void)start_bridge(filter);
	}
}

void obs_captions_filter_get_defaults(obs_data_t *settings)
{
	if (!settings) {
		return;
	}

	obs_data_set_default_string(settings, SETTING_TARGET_TEXT_SOURCE, "");
	obs_data_set_default_string(settings, SETTING_CONFIG_PATH, "");
	obs_data_set_default_string(settings, SETTING_SIDECAR_EXE, "");
}

obs_properties_t *obs_captions_filter_get_properties(void *data)
{
	(void)data;

	obs_properties_t *props = obs_properties_create();
	obs_properties_add_text(props, SETTING_TARGET_TEXT_SOURCE, "Target text source", OBS_TEXT_DEFAULT);
	obs_properties_add_text(props, SETTING_CONFIG_PATH, "Config path", OBS_TEXT_DEFAULT);
	obs_properties_add_text(props, SETTING_SIDECAR_EXE, "Sidecar executable", OBS_TEXT_DEFAULT);
	return props;
}

struct obs_audio_data *obs_captions_filter_filter_audio(void *data, struct obs_audio_data *audio)
{
	auto *filter = static_cast<obs_captions_filter_data *>(data);
	if (!filter || !audio || audio->frames == 0) {
		return audio;
	}

	if (!filter->bridge) {
		return audio;
	}

	struct obs_audio_info info = {};
	if (!obs_get_audio_info(&info)) {
		return audio;
	}

	const uint32_t sample_rate = info.samples_per_sec;
	filter->sample_rate = sample_rate;
	if (sample_rate == TARGET_SAMPLE_RATE) {
		destroy_resampler(filter);
	} else if (!ensure_resampler_to_16k(filter, sample_rate)) {
		return audio;
	}

	std::array<float, obs_native_ipc::AudioSlot::kMaxSamples> mono{};
	size_t present_channels = 0u;
	for (uint32_t offset = 0u; offset < audio->frames;) {
		const uint32_t chunk_frames = std::min<uint32_t>(
			static_cast<uint32_t>(mono.size()), audio->frames - offset);
		present_channels = downmix_planar_to_mono(audio->data, offset, chunk_frames, mono.data());
		if (present_channels == 0u) {
			break;
		}
		if (sample_rate == TARGET_SAMPLE_RATE) {
			filter->bridge->push_audio(mono.data(), chunk_frames, 1u);
		} else {
			push_resampled_audio(filter, mono.data(), chunk_frames);
		}
		offset += chunk_frames;
	}
	if (present_channels > 0u) {
		filter->channels = present_channels;
	}
	return audio;
}
