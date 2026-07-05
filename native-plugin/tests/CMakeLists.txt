cmake_minimum_required(VERSION 3.28)

project(obs_captions_ipc_row3_tests LANGUAGES CXX)

set(COMMON_SOURCES
	../src/framing.cpp
	../src/ring.cpp
	../src/epoch_gate.cpp
	../src/out_queue.cpp
	../src/quiesce.cpp
)

add_executable(framing_test framing_test.cpp ${COMMON_SOURCES})
add_executable(ring_test ring_test.cpp ${COMMON_SOURCES})
add_executable(writer_serialize_test writer_serialize_test.cpp ${COMMON_SOURCES})
add_executable(epoch_gate_test epoch_gate_test.cpp ${COMMON_SOURCES})
add_executable(quiesce_test quiesce_test.cpp ${COMMON_SOURCES})

foreach(test_exec framing_test ring_test writer_serialize_test epoch_gate_test quiesce_test)
	target_include_directories(${test_exec} PRIVATE ../src ../tests)
	target_compile_features(${test_exec} PRIVATE cxx_std_17)
endforeach()
