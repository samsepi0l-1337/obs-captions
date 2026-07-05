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
)

TESTS=(
	"${SCRIPT_DIR}/framing_test.cpp"
	"${SCRIPT_DIR}/ring_test.cpp"
	"${SCRIPT_DIR}/writer_serialize_test.cpp"
	"${SCRIPT_DIR}/epoch_gate_test.cpp"
	"${SCRIPT_DIR}/quiesce_test.cpp"
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

