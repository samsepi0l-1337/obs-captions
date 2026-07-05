# SPDX-License-Identifier: GPL-2.0
cmake_minimum_required(VERSION 3.20)

project(pipe_transport_poc CXX)

find_package(Threads REQUIRED)

add_executable(pipe_transport_poc pipe_transport_poc.cpp)
set_target_properties(pipe_transport_poc PROPERTIES CXX_STANDARD 17 CXX_STANDARD_REQUIRED YES)
target_link_libraries(pipe_transport_poc PRIVATE Threads::Threads)
