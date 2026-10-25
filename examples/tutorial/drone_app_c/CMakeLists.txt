# The vehicle of the WireForge tutorial in C99: MAVLink 2 over UDP with the C code generated from
# your model. The same vehicle.c runs on a microcontroller in ../drone_firmware.
#   cmake -S drone_app_c -B build/drone_app_c -DDRONE_PROJECT=$PWD/drone -DWireForge_DIR=$(wireforge cmake-dir)
#   cmake --build build/drone_app_c && build/drone_app_c/drone_sim_c
# Linux and macOS (POSIX sockets).
cmake_minimum_required(VERSION 3.21)
project(drone_app_c LANGUAGES C)

set(DRONE_PROJECT "" CACHE PATH "The MAVLink 2 project of the tutorial (directory with project.yaml)")
if(NOT DRONE_PROJECT)
  message(FATAL_ERROR "Set DRONE_PROJECT to your project, for example -DDRONE_PROJECT=$PWD/drone")
endif()

find_package(WireForge 0.4 REQUIRED)
wireforge_generate(drone_model PROJECT "${DRONE_PROJECT}" LANGUAGE c)

add_executable(drone_sim_c main.c vehicle.c)
set_target_properties(drone_sim_c PROPERTIES C_STANDARD 99 C_STANDARD_REQUIRED ON)
target_link_libraries(drone_sim_c PRIVATE drone_model m)
target_compile_options(drone_sim_c PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Werror)
