// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

// Shared obs_data setting-id strings for the captions filter properties
// panel (obs-captions-properties.cpp) and its update() reader
// (obs-captions-filter.cpp). Libobs-free — plain string constants.

namespace obs_captions_settings {

constexpr const char *kTargetTextSource = "target_text_source";
constexpr const char *kSidecarExe = "sidecar_exe";
constexpr const char *kEngine = "engine";
constexpr const char *kShowAdvanced = "show_advanced";
constexpr const char *kLanguage = "language";
constexpr const char *kLocalModelSize = "local_model_size";
constexpr const char *kLocalDevice = "local_device";
constexpr const char *kApiKey = "api_key";
constexpr const char *kProviderModel = "provider_model";
constexpr const char *kAzureRegion = "azure_region";
constexpr const char *kSecretWarning = "secret_warning";
constexpr const char *kValidateKey = "validate_key";
constexpr const char *kValidateStatus = "validate_status";
constexpr const char *kSuppressBlank = "suppress_blank";
constexpr const char *kFilterWords = "filter_words";
constexpr const char *kSuppressRegex = "suppress_regex";

} // namespace obs_captions_settings
