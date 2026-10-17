# An application that uses WireForge through its CMake package:
#   cmake -S examples/cmake_consumer -B build/consumer -DWireForge_DIR=$(wireforge cmake-dir)
#   cmake --build build/consumer && ctest --test-dir build/consumer
cmake_minimum_required(VERSION 3.21)
project(wireforge_consumer LANGUAGES CXX)

find_package(WireForge 0.1 REQUIRED)
wireforge_generate(telemetry_model PROJECT "${CMAKE_CURRENT_SOURCE_DIR}/../01_basic_telemetry")

add_executable(consumer main.cpp)
target_link_libraries(consumer PRIVATE telemetry_model)
if(MSVC)
  target_compile_options(consumer PRIVATE /W4 /WX)
else()
  target_compile_options(consumer PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Werror)
endif()

enable_testing()
add_test(NAME consumer COMMAND consumer)
