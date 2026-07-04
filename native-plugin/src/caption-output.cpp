// SPDX-License-Identifier: GPL-2.0-or-later
#include <obs-module.h>
#include "caption-output.h"

bool send_caption_to_source(const char *source_name, const char *caption)
{
	if (!source_name || !*source_name) {
		return false;
	}

	if (!caption) {
		return false;
	}

	obs_source_t *target = obs_get_source_by_name(source_name);
	if (!target) {
		return false;
	}

	obs_data_t *settings = obs_source_get_settings(target);
	if (!settings) {
		obs_source_release(target);
		return false;
	}

	obs_data_set_string(settings, "text", caption);
	obs_source_update(target, settings);
	obs_data_release(settings);
	obs_source_release(target);
	return true;
}
