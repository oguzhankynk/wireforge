# A ground station in C99 for ArduPilot, with the C code generated from the WireForge import of ardupilotmega.xml.
#   wireforge import-mavlink ardupilotmega.xml ardupilot --name ardupilot
#   cmake -S ardupilot_gcs -B build/ardupilot_gcs -DARDUPILOT_PROJECT=$PWD/ardupilot -DWireForge_DIR=$(wireforge cmake-dir)
#   cmake --build build/ardupilot_gcs && build/ardupilot_gcs/ardupilot_gcs
# Linux and macOS (POSIX sockets).
cmake_minimum_required(VERSION 3.21)
project(ardupilot_gcs LANGUAGES C)

set(ARDUPILOT_PROJECT "" CACHE PATH "The import of ardupilotmega.xml (directory with project.yaml)")
if(NOT ARDUPILOT_PROJECT)
  message(FATAL_ERROR "Set ARDUPILOT_PROJECT to the import, for example -DARDUPILOT_PROJECT=$PWD/ardupilot")
endif()

find_package(WireForge 0.4 REQUIRED)
wireforge_generate(ardupilot_model PROJECT "${ARDUPILOT_PROJECT}" LANGUAGE c)

add_executable(ardupilot_gcs main.c)
set_target_properties(ardupilot_gcs PROPERTIES C_STANDARD 99 C_STANDARD_REQUIRED ON)
target_link_libraries(ardupilot_gcs PRIVATE ardupilot_model)
target_compile_options(ardupilot_gcs PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Werror)
