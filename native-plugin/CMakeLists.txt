cmake_minimum_required(VERSION 3.28)

project(obs-captions-native-plugin LANGUAGES C CXX)

find_package(libobs REQUIRED)

add_library(obs-captions MODULE)
set_target_properties(
	obs-captions
	PROPERTIES
		PREFIX ""
		OUTPUT_NAME "obs-captions"
)

target_sources(
	obs-captions
	PRIVATE
		src/plugin-main.c
		src/obs-captions-filter.c
		src/obs-captions-filter.cpp
		src/obs-captions-properties.cpp
		src/caption-output.cpp
		src/ipc-bridge.cpp
		src/ipc-bridge-core.cpp
		src/ipc-bridge-internal.cpp
		src/ipc-bridge-teardown.cpp
		src/ipc-transport.cpp
		src/framing.cpp
		src/ring.cpp
		src/epoch_gate.cpp
		src/out_queue.cpp
		src/quiesce.cpp
		src/plugin-settings.cpp
		src/ipc-bridge.hpp
		src/obs-captions-properties.hpp
		src/obs-captions-setting-ids.hpp
		src/plugin-settings.hpp
)

target_include_directories(obs-captions PRIVATE src)

target_link_libraries(obs-captions PRIVATE OBS::libobs)

target_compile_features(obs-captions PRIVATE c_std_11 cxx_std_17)

# TODO: Replace with obs-plugintemplate cmake/common helper flow in the Windows build path.
install(TARGETS obs-captions LIBRARY DESTINATION obs-plugins/64bit RUNTIME DESTINATION obs-plugins/64bit)
install(DIRECTORY data/ DESTINATION data/obs-plugins/obs-captions)
