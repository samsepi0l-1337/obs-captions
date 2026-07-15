# OBS Plugin Settings Page Implementation Plan (P2)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or executing-plans. Steps use checkbox syntax.

**Goal:** Give the OBS native plugin a LocalVocal-style settings panel (engine dropdown local+cloud, model, language, masked API keys, target text source) that serializes settings to the sidecar and injects keys via environment.

**Architecture:** The C++ filter builds a richer `obs_properties` panel; on update it (a) generates a sidecar config file from its OBS settings and (b) injects cloud API keys into the child process environment. The pure logic — settings→config serialization and engine→visible-fields mapping — is split into libobs-free functions that unit-test in the existing native harness; the `obs_properties`/`obs_data` glue compiles only where libobs is present (Windows CI).

**Tech Stack:** C++17, OBS `obs_properties`/`obs_data` API, existing `IpcBridge`/`ChildTransport`.

## Global Constraints
- Files ≤ 350 lines; pure logic separated from libobs glue.
- API keys go to the child process **environment**, never written into the generated config file on disk.
- OBS-persisted property values (incl. `OBS_TEXT_PASSWORD`) are plaintext in the scene JSON — accepted tradeoff; show a warning info label.
- Plugin exposes only plugin-relevant settings: engine/model/language/keys/text-processing/target-text-source (NOT audio/sink/server/overlay).
- Native tests build without libobs (like the current suite); new pure-logic tests must too.

## File Structure
- `native-plugin/src/plugin-settings.hpp/.cpp` (new) — libobs-free pure logic: `struct PluginSettings` (engine, language, model_size, device, provider_model, text-processing, target source) + `std::string to_sidecar_toml(const PluginSettings&)` + `std::vector<std::string> visible_field_ids(const std::string& engine)` + `std::vector<std::pair<std::string,std::string>> env_for(const PluginSettings&)` (var,value for the selected cloud key).
- `native-plugin/src/ipc-transport.hpp/.cpp` (modify) — add `std::vector<std::pair<std::string,std::string>> env` to `SpawnConfig`; apply on spawn (POSIX: `setenv` in child before `execvp`; Windows: merge into CreateProcess env block).
- `native-plugin/src/obs-captions-filter.cpp` (modify) — expand `obs_captions_filter_get_properties` (LocalVocal-style groups + engine `modified_callback`); in `update`, build `PluginSettings` from `obs_data`, write generated config to `obs_module_config_path`, set `cfg.spawn.env`, restart bridge.
- `native-plugin/data/locale/en-US.ini` (modify) — new labels.
- `native-plugin/tests/plugin_settings_test.cpp` (new) + `run_tests.sh` (add).

---

## Task 1: Pure settings logic (libobs-free)

**Files:** Create `native-plugin/src/plugin-settings.hpp` + `.cpp`, `native-plugin/tests/plugin_settings_test.cpp`; modify `native-plugin/tests/run_tests.sh`.

**Interfaces:**
- `struct PluginSettings { std::string engine, language, local_model_size, local_device, provider_model, target_text_source; bool suppress_blank; std::vector<std::string> filter_words, suppress_regex; std::string api_key; };`
- `std::string to_sidecar_toml(const PluginSettings&)` — emits a TOML the Python sidecar's `load_config` accepts (`engine`, `language`, `[local] model_size/device`, `[providers.<engine>] model`, `[text] ...`). No key in output.
- `std::vector<std::string> visible_field_ids(const std::string& engine)` — `{"local_model_size","local_device"}` for `local`; `{"api_key","provider_model"}` (+`azure_region` for azure) otherwise.
- `env_for(const PluginSettings&) -> std::vector<std::pair<std::string,std::string>>` — maps engine→env var (`openai→OPENAI_API_KEY`, `deepgram→DEEPGRAM_API_KEY`, `google→GEMINI_API_KEY`, `azure→AZURE_SPEECH_KEY`, …) with `api_key` value; empty for `local` or empty key.

- [ ] Step 1: Write `plugin_settings_test.cpp` asserting: `to_sidecar_toml` for a local+medium settings contains `engine = "local"` and `model_size = "medium"` and NOT the api_key; for deepgram contains `engine = "deepgram"`; `visible_field_ids("local")` vs `("openai")` differ correctly; `env_for` returns `{"DEEPGRAM_API_KEY","k"}` for deepgram, empty for local.
- [ ] Step 2: Build+run per run_tests.sh convention (clang++ -std=c++17 -fsanitize=address,undefined, the test + plugin-settings.cpp) → fails (not implemented).
- [ ] Step 3: Implement `plugin-settings.hpp/.cpp` (no libobs include).
- [ ] Step 4: Add to `run_tests.sh` TESTS list; run whole suite → PASS.
- [ ] Step 5: Commit.

---

## Task 2: SpawnConfig env injection

**Files:** Modify `native-plugin/src/ipc-transport.hpp` (add `env`), `native-plugin/src/ipc-transport.cpp` (apply env on spawn, both POSIX and Windows branches). Test: extend `native-plugin/tests/ipc_transport_test.cpp`.

**Interfaces:** `SpawnConfig { std::vector<std::string> argv; std::vector<std::pair<std::string,std::string>> env; }`. POSIX child: for each pair `setenv(k.c_str(), v.c_str(), 1)` before `execvp`. Windows: build merged environment block for `CreateProcess`.

- [ ] Step 1: Add a test: spawn the fake-child with `env={{"OBS_CAPTIONS_TEST_ENV","xyz"}}` and a `--fake-child print-env` mode that writes the var back; assert transport reads "xyz". (Or a POSIX-only unit around a helper that composes the child env.)
- [ ] Step 2: Build+run → fail.
- [ ] Step 3: Implement env on both spawn paths.
- [ ] Step 4: Run native suite (incl. TSan) → PASS.
- [ ] Step 5: Commit.

---

## Task 3: obs_properties panel + update wiring (libobs-gated)

**Files:** Modify `native-plugin/src/obs-captions-filter.cpp`, `native-plugin/src/obs-captions-filter.h`, `native-plugin/data/locale/en-US.ini`.

**Interfaces:** Rebuild `obs_captions_filter_get_properties`: General group (target_text_source list via `obs_enum_sources`, language, sidecar_exe path), Engine list (`local`+10 cloud) with `obs_property_set_modified_callback` calling `obs_property_set_visible` per `visible_field_ids`; local group (model_size list, device list); cloud group (`api_key` `OBS_TEXT_PASSWORD`, `provider_model`, azure_region, secret-warning `OBS_TEXT_INFO`); advanced Text group (suppress_blank bool, filter_words/suppress_regex multiline). In `obs_captions_filter_update`: read `obs_data` into `PluginSettings`, `to_sidecar_toml` → write to `obs_module_config_path("obs-captions.generated.toml")`, set `cfg.spawn.argv={exe,"ipc-sidecar","--config",generated}` + `cfg.spawn.env=env_for(settings)`, restart bridge.

- [ ] Step 1: Update `obs-captions-filter.h` settings struct fields.
- [ ] Step 2: Implement properties builder + engine callback (uses `visible_field_ids`).
- [ ] Step 3: Implement update → generate config + env + restart (uses `to_sidecar_toml`,`env_for`).
- [ ] Step 4: Add locale labels.
- [ ] Step 5: This compiles only with libobs → validated by the Windows CI plugin build (`build_plugin_windows.ps1`). Commit; rely on CI `Windows plugin package` job.

---

## Self-Review notes
- Pure logic (T1) + env plumbing (T2) are locally unit-tested (no libobs) in the native harness — real verification.
- T3 (obs_properties glue) compiles only under libobs → verified by the existing Windows plugin CI job, not locally (documented env gate, same as the rest of the plugin DLL).
- Keys: `to_sidecar_toml` never emits the key (T1 asserts); keys flow only via `env_for` → `SpawnConfig.env` (T2). Matches the security constraint.
- Deferred: model auto-download UX (not needed — faster-whisper downloads); translation (out of scope).
