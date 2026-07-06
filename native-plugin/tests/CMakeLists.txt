cmake_minimum_required(VERSION 3.28)

project(obs_captions_ipc_row3_tests LANGUAGES CXX)

set(COMMON_SOURCES
	../src/framing.cpp
	../src/ring.cpp
	../src/epoch_gate.cpp
	../src/out_queue.cpp
	../src/quiesce.cpp
	../src/ipc-transport.cpp
	../src/ipc-bridge.cpp
	../src/ipc-bridge-core.cpp
	../src/ipc-bridge-internal.cpp
)

add_executable(framing_test framing_test.cpp ${COMMON_SOURCES})
add_executable(ring_test ring_test.cpp ${COMMON_SOURCES})
add_executable(writer_serialize_test writer_serialize_test.cpp ${COMMON_SOURCES})
add_executable(epoch_gate_test epoch_gate_test.cpp ${COMMON_SOURCES})
add_executable(quiesce_test quiesce_test.cpp ${COMMON_SOURCES})
add_executable(ipc_transport_test ipc_transport_test.cpp ${COMMON_SOURCES})
add_executable(ipc_bridge_test ipc_bridge_test.cpp ipc_bridge_test_helpers.cpp ${COMMON_SOURCES})

option(IPC_TRANSPORT_TSAN "Build ipc_transport_test with thread sanitizer" OFF)

foreach(test_exec framing_test ring_test writer_serialize_test epoch_gate_test quiesce_test ipc_transport_test ipc_bridge_test)
	target_include_directories(${test_exec} PRIVATE ../src ../tests)
	target_compile_features(${test_exec} PRIVATE cxx_std_17)
endforeach()

if (IPC_TRANSPORT_TSAN AND UNIX)
	target_compile_options(ipc_transport_test PRIVATE -fsanitize=thread -fno-omit-frame-pointer -O1 -g)
	target_link_options(ipc_transport_test PRIVATE -fsanitize=thread)
endif()
