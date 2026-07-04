// SPDX-License-Identifier: GPL-2.0-or-later
#include <obs-module.h>

OBS_DECLARE_MODULE()
OBS_MODULE_USE_DEFAULT_LOCALE("obs-captions", "en-US")

const char *obs_module_description(void)
{
	return obs_module_text("ObsCaptionsFilterDescription");
}

extern struct obs_source_info obs_captions_filter_info;

bool obs_module_load(void)
{
	obs_register_source(&obs_captions_filter_info);
	blog(LOG_INFO, "obs-captions: native audio filter loaded");
	return true;
}

void obs_module_unload(void)
{
	// no module-level resources to release
}
