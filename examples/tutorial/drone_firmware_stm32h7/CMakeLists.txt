# The vehicle of the WireForge tutorial on a real board, the NUCLEO-H743ZI2: the C code generated
# from your model and ../drone_app_c/vehicle.c, MAVLink 2 over the ST-LINK's serial port.
#   cmake -S drone_firmware_stm32h7 -B build/stm32h7 -DDRONE_PROJECT=$PWD/drone -DWireForge_DIR=$(wireforge cmake-dir) \
#     -DCMAKE_TOOLCHAIN_FILE=<wireforge>/cmake/toolchains/arm-none-eabi-cortex-m7.cmake
#   cmake --build build/stm32h7
#   pyocd flash -t stm32h743xx build/stm32h7/drone_firmware.elf
cmake_minimum_required(VERSION 3.21)
project(drone_firmware_stm32h7 LANGUAGES C)

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
target_link_options(drone_firmware.elf PRIVATE "-T${CMAKE_CURRENT_SOURCE_DIR}/stm32h743.ld"
                    "-Wl,-Map=drone_firmware.map")
set_target_properties(drone_firmware.elf PROPERTIES LINK_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/stm32h743.ld")
