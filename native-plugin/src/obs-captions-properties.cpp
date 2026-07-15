// SPDX-License-Identifier: GPL-2.0-or-later
#include "obs-captions-properties.hpp"
#include "obs-captions-setting-ids.hpp"
#include "plugin-settings.hpp"

#include <algorithm>
#include <array>
#include <cstring>
#include <string>

namespace {

using obs_captions_settings::kApiKey;
using obs_captions_settings::kAzureRegion;
using obs_captions_settings::kEngine;
using obs_captions_settings::kFilterWords;
using obs_captions_settings::kLanguage;
using obs_captions_settings::kLocalDevice;
using obs_captions_settings::kLocalModelSize;
using obs_captions_settings::kProviderModel;
using obs_captions_settings::kSecretWarning;
using obs_captions_settings::kSidecarExe;
using obs_captions_settings::kSuppressBlank;
using obs_captions_settings::kSuppressRegex;
using obs_captions_settings::kTargetTextSource;

// engine id -> locale key (see data/locale/en-US.ini), local + the 10 cloud
// engines the Python sidecar's AppConfig.engine literal accepts.
constexpr std::array<std::pair<const char *, const char *>, 11> kEngineOptions = {{
	{"local", "EngineLocal"},
	{"openai", "EngineOpenai"},
	{"elevenlabs", "EngineElevenlabs"},
	{"google", "EngineGoogle"},
	{"xai", "EngineXai"},
	{"deepgram", "EngineDeepgram"},
	{"assemblyai", "EngineAssemblyai"},
	{"azure", "EngineAzure"},
	{"openrouter", "EngineOpenrouter"},
	{"replicate", "EngineReplicate"},
	{"groq", "EngineGroq"},
}};

constexpr std::array<const char *, 5> kLocalModelSizes = {"tiny", "base", "small", "medium", "large-v3"};

constexpr std::array<std::pair<const char *, const char *>, 3> kLocalDevices = {{
	{"auto", "DeviceAuto"},
	{"cpu", "DeviceCpu"},
	{"cuda", "DeviceCuda"},
}};

// Property ids whose visibility is toggled per selected engine (all live
// directly on the top-level `props`, not inside a nested obs_properties_t
// group, so obs_properties_get() below can always find them).
constexpr std::array<const char *, 5> kEngineGatedFields = {
	kLocalModelSize, kLocalDevice, kApiKey, kProviderModel, kAzureRegion,
};

bool enum_text_source_callback(void *param, obs_source_t *source)
{
	auto *list = static_cast<obs_property_t *>(param);
	const char *id = source ? obs_source_get_id(source) : nullptr;
	if (!id) {
		return true;
	}
	const bool is_text_source = std::strcmp(id, "text_ft2_source_v2") == 0 ||
				     std::strcmp(id, "text_gdiplus_v2") == 0 ||
				     std::strcmp(id, "text_ft2_source") == 0 ||
				     std::strcmp(id, "text_gdiplus") == 0;
	if (!is_text_source) {
		return true;
	}
	const char *name = obs_source_get_name(source);
	if (name) {
		obs_property_list_add_string(list, name, name);
	}
	return true;
}

void apply_engine_visibility(obs_properties_t *props, const std::string &engine)
{
	const std::vector<std::string> visible = obs_native_ipc::visible_field_ids(engine);
	for (const char *field : kEngineGatedFields) {
		obs_property_t *prop = obs_properties_get(props, field);
		if (!prop) {
			continue;
		}
		const bool show = std::find(visible.begin(), visible.end(), field) != visible.end();
		obs_property_set_visible(prop, show);
	}
}

bool engine_modified_callback(obs_properties_t *props, obs_property_t *property, obs_data_t *settings)
{
	(void)property;
	const char *engine = obs_data_get_string(settings, kEngine);
	apply_engine_visibility(props, engine ? engine : "local");
	return true;
}

} // namespace

obs_properties_t *build_captions_properties(obs_captions_filter_data *filter)
{
	obs_properties_t *props = obs_properties_create();

	// General
	obs_property_t *target_list = obs_properties_add_list(props, kTargetTextSource,
							       obs_module_text("TargetTextSource"),
							       OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_STRING);
	obs_property_list_add_string(target_list, obs_module_text("None"), "");
	obs_enum_sources(enum_text_source_callback, target_list);

	obs_properties_add_text(props, kLanguage, obs_module_text("Language"), OBS_TEXT_DEFAULT);
	obs_properties_add_path(props, kSidecarExe, obs_module_text("SidecarExecutable"), OBS_PATH_FILE, nullptr,
				 nullptr);

	obs_property_t *engine_list = obs_properties_add_list(props, kEngine, obs_module_text("Engine"),
							       OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_STRING);
	for (const auto &option : kEngineOptions) {
		obs_property_list_add_string(engine_list, obs_module_text(option.second), option.first);
	}
	obs_property_set_modified_callback(engine_list, engine_modified_callback);

	// Local-engine-only
	obs_property_t *model_size_list = obs_properties_add_list(props, kLocalModelSize,
								   obs_module_text("LocalModelSize"),
								   OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_STRING);
	for (const char *size : kLocalModelSizes) {
		obs_property_list_add_string(model_size_list, size, size);
	}
	obs_property_t *device_list = obs_properties_add_list(props, kLocalDevice, obs_module_text("LocalDevice"),
							       OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_STRING);
	for (const auto &option : kLocalDevices) {
		obs_property_list_add_string(device_list, obs_module_text(option.second), option.first);
	}

	// Cloud-engine-only
	obs_properties_add_text(props, kApiKey, obs_module_text("ApiKey"), OBS_TEXT_PASSWORD);
	obs_properties_add_text(props, kProviderModel, obs_module_text("ProviderModel"), OBS_TEXT_DEFAULT);
	obs_properties_add_text(props, kAzureRegion, obs_module_text("AzureRegion"), OBS_TEXT_DEFAULT);
	obs_properties_add_text(props, kSecretWarning, obs_module_text("SecretWarning"), OBS_TEXT_INFO);

	// Advanced: text processing
	obs_properties_add_bool(props, kSuppressBlank, obs_module_text("SuppressBlank"));
	obs_properties_add_text(props, kFilterWords, obs_module_text("FilterWords"), OBS_TEXT_MULTILINE);
	obs_properties_add_text(props, kSuppressRegex, obs_module_text("SuppressRegex"), OBS_TEXT_MULTILINE);

	std::string current_engine = "local";
	if (filter && filter->context) {
		if (obs_data_t *current_settings = obs_source_get_settings(filter->context)) {
			const char *engine = obs_data_get_string(current_settings, kEngine);
			if (engine && *engine) {
				current_engine = engine;
			}
			obs_data_release(current_settings);
		}
	}
	apply_engine_visibility(props, current_engine);

	return props;
}
