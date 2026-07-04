// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

/**
 * Python sidecar IPC stub.
 * Planned transport: named pipe or local socket (Windows/macOS/Linux 모두 대응).
 */

#include <cstddef>

class PythonSttBridge {
public:
	using CaptionCallback = void (*)(const char *caption);

	// TODO(ipc): implement sidecar connection lifecycle.
	bool start() { return false; }

	// TODO(ipc): stop/cleanup transport thread and sockets.
	void stop() {}

	// TODO(ipc): serialize/interleave PCM frames and send to sidecar.
	bool send_pcm(const float *, std::size_t, std::size_t) { return false; }

	// TODO(ipc): register callback to receive caption text.
	void set_caption_callback(CaptionCallback callback) { caption_callback_ = callback; }

private:
	CaptionCallback caption_callback_ = nullptr;
};
