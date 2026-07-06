// SPDX-License-Identifier: GPL-2.0

#pragma once

#include "ipc-bridge.hpp"

#include <string>

void assert_true(bool cond, const char *message);
void child_log(const char *message);
obs_native_ipc::SpawnConfig fake_config(const char *argv0, const char *mode);
int run_fake_child(const std::string &mode);
