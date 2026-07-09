// SPDX-License-Identifier: GPL-2.0

#include "ipc_bridge_test_helpers.hpp"

#include <cstdio>
#include <cstdlib>

namespace {
std::FILE *child_log_file()
{
	static std::FILE *fp = nullptr;
	static bool initialized = false;
	if (!initialized) {
		initialized = true;
		const char *path = std::getenv("OBS_BRIDGE_TEST_CHILD_LOG");
		if (path && path[0] != '\0') {
			fp = std::fopen(path, "a");
		} else {
			fp = reinterpret_cast<std::FILE *>(-1);
		}
	}
	return (fp == reinterpret_cast<std::FILE *>(-1)) ? nullptr : fp;
}
}

void child_log(const char *message)
{
	if (std::FILE *fp = child_log_file()) {
		std::fputs(message, fp);
		std::fputc('\n', fp);
		std::fflush(fp);
	}
}
