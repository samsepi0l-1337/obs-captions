// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

// Libobs-gated: builds the obs_properties_t panel for the captions filter.
// Split out of obs-captions-filter.cpp to keep both files well under the
// project's file-size guideline; only compiles where libobs headers are
// available (see obs-captions-filter.h / CMakeLists.txt).
#include "obs-captions-filter.h"

obs_properties_t *build_captions_properties(obs_captions_filter_data *filter);
