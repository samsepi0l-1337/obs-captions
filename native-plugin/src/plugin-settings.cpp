// SPDX-License-Identifier: GPL-2.0
#include "plugin-settings.hpp"

#include <unordered_map>

namespace {

std::string escape_toml(const std::string &value)
{
	std::string out;
	out.reserve(value.size());
	for (char c : value) {
		if (c == '\\' || c == '"') {
			out.push_back('\\');
		}
		out.push_back(c);
	}
	return out;
}

std::string toml_string(const std::string &value)
{
	return "\"" + escape_toml(value) + "\"";
}

std::string toml_string_array(const std::vector<std::string> &values)
{
	std::string out = "[";
	for (std::size_t i = 0; i < values.size(); ++i) {
		if (i > 0) {
			out += ", ";
		}
		out += toml_string(values[i]);
	}
	out += "]";
	return out;
}

const std::unordered_map<std::string, std::string> &env_var_by_engine()
{
	static const std::unordered_map<std::string, std::string> table{
		{"openai", "OPENAI_API_KEY"},       {"elevenlabs", "ELEVENLABS_API_KEY"},
		{"google", "GEMINI_API_KEY"},       {"xai", "XAI_API_KEY"},
		{"deepgram", "DEEPGRAM_API_KEY"},   {"assemblyai", "ASSEMBLYAI_API_KEY"},
		{"azure", "AZURE_SPEECH_KEY"},      {"openrouter", "OPENROUTER_API_KEY"},
		{"replicate", "REPLICATE_API_TOKEN"}, {"groq", "GROQ_API_KEY"},
	};
	return table;
}

} // namespace

namespace obs_native_ipc {

std::string to_sidecar_toml(const PluginSettings &settings)
{
	std::string toml;
	toml += "engine = " + toml_string(settings.engine) + "\n";
	toml += "language = " + toml_string(settings.language) + "\n";

	toml += "\n[local]\n";
	toml += "model_size = " + toml_string(settings.local_model_size) + "\n";
	toml += "device = " + toml_string(settings.local_device) + "\n";

	if (settings.engine != "local" &&
	    (!settings.provider_model.empty() ||
	     (settings.engine == "azure" && !settings.azure_region.empty()))) {
		toml += "\n[providers." + settings.engine + "]\n";
		if (!settings.provider_model.empty()) {
			toml += "model = " + toml_string(settings.provider_model) + "\n";
		}
		if (settings.engine == "azure" && !settings.azure_region.empty()) {
			toml += "region = " + toml_string(settings.azure_region) + "\n";
		}
	}

	toml += "\n[text]\n";
	toml += "suppress_blank = ";
	toml += settings.suppress_blank ? "true" : "false";
	toml += "\n";
	toml += "filter_words = " + toml_string_array(settings.filter_words) + "\n";
	toml += "suppress_regex = " + toml_string_array(settings.suppress_regex) + "\n";

	return toml;
}

std::vector<std::string> visible_field_ids(const std::string &engine)
{
	if (engine == "local") {
		return {"local_model_size", "local_device"};
	}
	std::vector<std::string> fields{"api_key", "provider_model"};
	if (engine == "azure") {
		fields.push_back("azure_region");
	}
	return fields;
}

std::vector<std::string> advanced_field_ids()
{
	// local_device / azure_region are also engine-gated; the three text-
	// processing fields are always shown w.r.t. engine. The properties glue
	// ANDs these with engine gating (see obs-captions-properties.cpp).
	return {"local_device", "azure_region", "suppress_blank", "filter_words", "suppress_regex"};
}

std::vector<std::pair<std::string, std::string>> env_for(const PluginSettings &settings)
{
	if (settings.engine == "local" || settings.api_key.empty()) {
		return {};
	}
	const auto &table = env_var_by_engine();
	const auto it = table.find(settings.engine);
	if (it == table.end()) {
		return {};
	}
	return {{it->second, settings.api_key}};
}

std::vector<std::string> split_settings_lines(const std::string &text)
{
	std::vector<std::string> lines;
	std::string current;
	for (std::size_t i = 0; i <= text.size(); ++i) {
		const bool at_end = i == text.size();
		const char c = at_end ? '\n' : text[i];
		if (c != '\n') {
			current.push_back(c);
			continue;
		}
		while (!current.empty() && (current.back() == '\r' || current.back() == ' ' || current.back() == '\t')) {
			current.pop_back();
		}
		const std::size_t start = current.find_first_not_of(" \t");
		if (start != std::string::npos) {
			lines.push_back(current.substr(start));
		}
		current.clear();
	}
	return lines;
}

} // namespace obs_native_ipc
