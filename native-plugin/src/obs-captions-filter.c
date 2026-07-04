// SPDX-License-Identifier: GPL-2.0-or-later
#include "obs-captions-filter.h"

struct obs_source_info obs_captions_filter_info = {
	.id = "obs_captions_audio_filter",
	.type = OBS_SOURCE_TYPE_FILTER,
	.output_flags = OBS_SOURCE_AUDIO,
	.get_name = obs_captions_filter_get_name,
	.create = obs_captions_filter_create,
	.destroy = obs_captions_filter_destroy,
	.update = obs_captions_filter_update,
	.get_defaults = obs_captions_filter_get_defaults,
	.get_properties = obs_captions_filter_get_properties,
	.filter_audio = obs_captions_filter_filter_audio,
};
