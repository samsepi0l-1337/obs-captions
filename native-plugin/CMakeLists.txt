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
		src/caption-output.cpp
		src/ipc-bridge.hpp
)

target_include_directories(obs-captions PRIVATE src)

target_link_libraries(obs-captions PRIVATE OBS::libobs)

target_compile_features(obs-captions PRIVATE c_std_11 cxx_std_17)

# TODO: Replace with obs-plugintemplate cmake/common helper flow in the Windows build path.
