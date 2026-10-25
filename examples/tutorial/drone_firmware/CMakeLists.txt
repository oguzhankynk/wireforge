# The vehicle of the WireForge tutorial as firmware for a Cortex-M4 (QEMU's mps2-an386 board): the C
# code generated from your model and ../drone_app_c/vehicle.c, MAVLink 2 over UART0.
#   cmake -S drone_firmware -B build/drone_firmware -DDRONE_PROJECT=$PWD/drone \
#     -DWireForge_DIR=$(wireforge cmake-dir) -DCMAKE_TOOLCHAIN_FILE=<wireforge>/cmake/toolchains/arm-none-eabi.cmake
#   cmake --build build/drone_firmware
#   qemu-system-arm -M mps2-an386 -cpu cortex-m4 -display none -semihosting-config enable=on,target=native \
#     -serial tcp:127.0.0.1:5760,server=on,wait=off -kernel build/drone_firmware/drone_firmware.elf
cmake_minimum_required(VERSION 3.21)
project(drone_firmware LANGUAGES C)

set(DRONE_PROJECT "" CACHE PATH "The MAVLink 2 project of the tutorial (directory with project.yaml)")
if(NOT DRONE_PROJECT)
  message(FATAL_ERROR "Set DRONE_PROJECT to your project, for example -DDRONE_PROJECT=$PWD/drone")
endif()

find_package(WireForge 0.4 REQUIRED)
wireforge_generate(drone_model PROJECT "${DRONE_PROJECT}" LANGUAGE c)

set(vehicle "${CMAKE_CURRENT_SOURCE_DIR}/../drone_app_c")
add_executable(drone_firmware.elf main.c startup.c "${vehicle}/vehicle.c")
set_target_properties(drone_firmware.elf PROPERTIES C_STANDARD 99 C_STANDARD_REQUIRED ON)
target_include_directories(drone_firmware.elf PRIVATE "${vehicle}")
target_link_libraries(drone_firmware.elf PRIVATE drone_model m)
target_compile_options(drone_firmware.elf PRIVATE -Wall -Wextra -Werror -Os)
target_link_options(drone_firmware.elf PRIVATE "-T${CMAKE_CURRENT_SOURCE_DIR}/mps2_an386.ld"
                    "-Wl,-Map=drone_firmware.map")
set_target_properties(drone_firmware.elf PROPERTIES LINK_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/mps2_an386.ld")
