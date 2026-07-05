#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

COMMON_SOURCES=(
	"${PROJECT_ROOT}/src/framing.cpp"
	"${PROJECT_ROOT}/src/ring.cpp"
	"${PROJECT_ROOT}/src/epoch_gate.cpp"
	"${PROJECT_ROOT}/src/out_queue.cpp"
	"${PROJECT_ROOT}/src/quiesce.cpp"
	"${PROJECT_ROOT}/src/ipc-transport.cpp"
)

TESTS=(
	"${SCRIPT_DIR}/framing_test.cpp"
	"${SCRIPT_DIR}/ring_test.cpp"
	"${SCRIPT_DIR}/writer_serialize_test.cpp"
	"${SCRIPT_DIR}/epoch_gate_test.cpp"
	"${SCRIPT_DIR}/quiesce_test.cpp"
	"${SCRIPT_DIR}/ipc_transport_test.cpp"
)

for test_file in "${TESTS[@]}"; do
	test_name="$(basename "${test_file}")"
	echo "==== run ${test_name} ===="
	clang++ -std=c++17 -O1 -fsanitize=address,undefined -pthread \
		"${test_file}" \
		"${COMMON_SOURCES[@]}" \
		-I"${PROJECT_ROOT}/src" \
		-I"${SCRIPT_DIR}" \
		-o /tmp/t
	/tmp/t
done

TSAN_BIN=/tmp/t_ipc_tsan
TSAN_TEST="${SCRIPT_DIR}/ipc_transport_test.cpp"
echo "==== run ipc_transport_test (thread sanitizer) ===="
clang++ -std=c++17 -O1 -g -fno-omit-frame-pointer -fsanitize=thread -pthread \
	"${TSAN_TEST}" \
	"${COMMON_SOURCES[@]}" \
	-I"${PROJECT_ROOT}/src" \
	-I"${SCRIPT_DIR}" \
	-o "${TSAN_BIN}"
"${TSAN_BIN}"
