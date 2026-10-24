# The vehicle of the WireForge tutorial: MAVLink 2 over UDP with the code generated from your model.
#   cmake -S drone_app -B build/drone_app -DDRONE_PROJECT=$PWD/drone -DWireForge_DIR=$(wireforge cmake-dir)
#   cmake --build build/drone_app && build/drone_app/drone_sim
# Linux and macOS (POSIX sockets).
cmake_minimum_required(VERSION 3.21)
project(drone_app LANGUAGES CXX)

set(DRONE_PROJECT "" CACHE PATH "The MAVLink 2 project of the tutorial (directory with project.yaml)")
if(NOT DRONE_PROJECT)
  message(FATAL_ERROR "Set DRONE_PROJECT to your project, for example -DDRONE_PROJECT=$PWD/drone")
endif()

find_package(WireForge 0.2 REQUIRED)
wireforge_generate(drone_model PROJECT "${DRONE_PROJECT}")

add_executable(drone_sim main.cpp)
target_compile_features(drone_sim PRIVATE cxx_std_17)
target_link_libraries(drone_sim PRIVATE drone_model)
target_compile_options(drone_sim PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Werror)
