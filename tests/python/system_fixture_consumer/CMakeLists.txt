# Consumers of two units of ../system_fixture through the CMake package: unit A (C++) and unit B
# (C, because the unit has no language of its own and LANGUAGE says c).
cmake_minimum_required(VERSION 3.21)
project(fixture_consumer LANGUAGES C CXX)

find_package(WireForge REQUIRED)
wireforge_generate(unit_a PROJECT "${CMAKE_CURRENT_SOURCE_DIR}/../system_fixture" NODE A)
wireforge_generate(unit_b PROJECT "${CMAKE_CURRENT_SOURCE_DIR}/../system_fixture" NODE B LANGUAGE c)

add_executable(consumer_a main_a.cpp)
target_link_libraries(consumer_a PRIVATE unit_a)
add_executable(consumer_b main_b.c)
target_link_libraries(consumer_b PRIVATE unit_b)

enable_testing()
add_test(NAME consumer_a COMMAND consumer_a)
add_test(NAME consumer_b COMMAND consumer_b)
