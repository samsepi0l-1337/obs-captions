// SPDX-License-Identifier: GPL-2.0
#include "plugin-settings.hpp"

#include <algorithm>
#include <iostream>
#include <string>

void assert_true(bool cond, const char *message)
{
	if (!cond) {
		std::cerr << "FAILED: " << message << std::endl;
		std::exit(1);
	}
}

bool contains(const std::string &haystack, const std::string &needle)
{
	return haystack.find(needle) != std::string::npos;
}

bool has_field(const std::vector<std::string> &fields, const std::string &id)
{
	return std::find(fields.begin(), fields.end(), id) != fields.end();
}

int main()
{
	using namespace obs_native_ipc;

	PluginSettings local_settings;
	local_settings.engine = "local";
	local_settings.local_model_size = "medium";
	local_settings.local_device = "cuda";
	local_settings.api_key = "should-never-appear";

	const std::string local_toml = to_sidecar_toml(local_settings);
	assert_true(contains(local_toml, "engine = \"local\""), "local toml should set engine");
	assert_true(contains(local_toml, "model_size = \"medium\""), "local toml should set model_size");
	assert_true(!contains(local_toml, "should-never-appear"), "toml must never emit api_key");
	assert_true(!contains(local_toml, "api_key"), "toml must never mention api_key key");

	PluginSettings deepgram_settings;
	deepgram_settings.engine = "deepgram";
	deepgram_settings.provider_model = "nova-2";
	deepgram_settings.api_key = "dg-secret";

	const std::string deepgram_toml = to_sidecar_toml(deepgram_settings);
	assert_true(contains(deepgram_toml, "engine = \"deepgram\""), "deepgram toml should set engine");
	assert_true(contains(deepgram_toml, "[providers.deepgram]"), "deepgram toml should have provider section");
	assert_true(contains(deepgram_toml, "model = \"nova-2\""), "deepgram toml should set provider model");
	assert_true(!contains(deepgram_toml, "dg-secret"), "deepgram toml must never emit api_key");

	PluginSettings azure_settings;
	azure_settings.engine = "azure";
	azure_settings.provider_model = "azure-model";
	azure_settings.azure_region = "eastus";
	azure_settings.api_key = "azure-secret";
	const std::string azure_toml = to_sidecar_toml(azure_settings);
	assert_true(contains(azure_toml, "[providers.azure]"), "azure toml should have provider section");
	assert_true(contains(azure_toml, "region = \"eastus\""), "azure toml should set region");
	assert_true(!contains(azure_toml, "azure-secret"), "azure toml must never emit api_key");

	const auto local_fields = visible_field_ids("local");
	assert_true(has_field(local_fields, "local_model_size"), "local fields should include local_model_size");
	assert_true(has_field(local_fields, "local_device"), "local fields should include local_device");
	assert_true(!has_field(local_fields, "api_key"), "local fields should not include api_key");

	const auto openai_fields = visible_field_ids("openai");
	assert_true(has_field(openai_fields, "api_key"), "openai fields should include api_key");
	assert_true(has_field(openai_fields, "provider_model"), "openai fields should include provider_model");
	assert_true(!has_field(openai_fields, "local_model_size"), "openai fields should not include local_model_size");
	assert_true(!has_field(openai_fields, "azure_region"), "openai fields should not include azure_region");

	const auto azure_fields = visible_field_ids("azure");
	assert_true(has_field(azure_fields, "azure_region"), "azure fields should include azure_region");

	PluginSettings deepgram_key;
	deepgram_key.engine = "deepgram";
	deepgram_key.api_key = "k";
	const auto deepgram_env = env_for(deepgram_key);
	assert_true(deepgram_env.size() == 1u, "deepgram env should have exactly one pair");
	assert_true(deepgram_env[0].first == "DEEPGRAM_API_KEY", "deepgram env var name should match");
	assert_true(deepgram_env[0].second == "k", "deepgram env value should match api_key");

	PluginSettings local_key;
	local_key.engine = "local";
	local_key.api_key = "unused";
	assert_true(env_for(local_key).empty(), "local engine should never inject env");

	PluginSettings empty_key;
	empty_key.engine = "openai";
	empty_key.api_key = "";
	assert_true(env_for(empty_key).empty(), "empty api_key should never inject env");

	const auto advanced = advanced_field_ids();
	assert_true(advanced.size() == 5u, "advanced_field_ids should expose exactly the tuning subset");
	assert_true(has_field(advanced, "local_device"), "advanced should include local_device");
	assert_true(has_field(advanced, "azure_region"), "advanced should include azure_region");
	assert_true(has_field(advanced, "suppress_blank"), "advanced should include suppress_blank");
	assert_true(has_field(advanced, "filter_words"), "advanced should include filter_words");
	assert_true(has_field(advanced, "suppress_regex"), "advanced should include suppress_regex");
	assert_true(!has_field(advanced, "engine"), "advanced must not include core engine");
	assert_true(!has_field(advanced, "language"), "advanced must not include core language");
	assert_true(!has_field(advanced, "local_model_size"), "advanced must not include core local_model_size");
	assert_true(!has_field(advanced, "api_key"), "advanced must not include core api_key");
	assert_true(!has_field(advanced, "provider_model"), "advanced must not include core provider_model");
	assert_true(!has_field(advanced, "target_text_source"), "advanced must not include core target_text_source");
	// Every advertised advanced id must be an id the plugin actually gates on
	// engine or an always-on text field — i.e. it must be reachable as either an
	// engine-gated field or one of the text-processing properties.
	for (const auto &id : advanced) {
		const bool engine_gated = id == "local_device" || id == "azure_region";
		const bool text_field = id == "suppress_blank" || id == "filter_words" || id == "suppress_regex";
		assert_true(engine_gated || text_field, "advanced id must map to a real plugin property");
	}

	const auto lines = split_settings_lines("  foo \r\n\nbar\n  \nbaz");
	assert_true(lines.size() == 3u, "split_settings_lines should skip blank lines");
	assert_true(lines[0] == "foo", "split_settings_lines should trim whitespace and CR");
	assert_true(lines[1] == "bar", "split_settings_lines should keep normal lines");
	assert_true(lines[2] == "baz", "split_settings_lines should flush a trailing line without a newline");
	assert_true(split_settings_lines("").empty(), "split_settings_lines on empty text should be empty");

	std::cout << "plugin_settings_test: PASS" << std::endl;
	return 0;
}
