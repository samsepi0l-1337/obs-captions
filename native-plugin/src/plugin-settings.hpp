// SPDX-License-Identifier: GPL-2.0
#pragma once

// Deliberately libobs-free: this header/impl pair unit-tests in the native
// harness (no libobs available there). Do not add any obs-module.h /
// obs-properties.h include here — that glue lives in obs-captions-filter.cpp.

#include <string>
#include <utility>
#include <vector>

namespace obs_native_ipc {

// Plugin-owned STT settings, mirrored from the OBS properties panel and
// serialized into the sidecar's TOML config. `api_key` is the one field that
// must NEVER be written to `to_sidecar_toml()` output — it flows only through
// `env_for()` into the child process environment (see ipc-transport SpawnConfig::env).
struct PluginSettings {
	std::string engine{"local"};
	std::string language{"ko"};
	std::string local_model_size{"small"};
	std::string local_device{"auto"};
	std::string provider_model;
	std::string azure_region;
	std::string target_text_source;
	bool suppress_blank{true};
	std::vector<std::string> filter_words;
	std::vector<std::string> suppress_regex;
	std::string api_key;
};

// Emits a TOML document loadable by the Python sidecar's `load_config()`
// (obs_captions.config.AppConfig). Never includes `api_key`.
std::string to_sidecar_toml(const PluginSettings &settings);

// Field ids the properties panel should show for the given engine:
// local -> local-only fields; any cloud engine -> api_key + provider_model
// (+ azure_region for "azure").
std::vector<std::string> visible_field_ids(const std::string &engine);

// Maps the settings' cloud engine + api_key to the (env var, value) pair the
// sidecar child process needs. Empty for engine=="local" or an empty key.
std::vector<std::pair<std::string, std::string>> env_for(const PluginSettings &settings);

// Splits multiline OBS text-field content (filter_words / suppress_regex
// text areas) into trimmed, non-empty lines. Libobs-free so it unit-tests
// alongside the rest of this file.
std::vector<std::string> split_settings_lines(const std::string &text);

} // namespace obs_native_ipc
