# Test harness of the runtime and the generated code on FreeRTOS (Cortex-M4F, QEMU mps2-an386).
# A project of its own, not part of the main build:
#   cmake -S tests/rtos -B build/rtos -G Ninja -DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/arm-none-eabi.cmake
# quality/run_freertos.sh configures, builds and runs it.
cmake_minimum_required(VERSION 3.20)
project(wireforge_rtos C CXX ASM)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
get_filename_component(WIREFORGE_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/../.. ABSOLUTE)

find_package(Python3 COMPONENTS Interpreter REQUIRED)

include(FetchContent)
set(FREERTOS_HEAP "4" CACHE STRING "")
set(FREERTOS_PORT "GCC_ARM_CM4F" CACHE STRING "")
add_library(freertos_config INTERFACE)
target_include_directories(freertos_config INTERFACE ${CMAKE_CURRENT_SOURCE_DIR})
FetchContent_Declare(freertos_kernel
  GIT_REPOSITORY https://github.com/FreeRTOS/FreeRTOS-Kernel.git
  GIT_TAG V11.1.0
  GIT_SHALLOW TRUE)
FetchContent_MakeAvailable(freertos_kernel)

# The generated self-test of an example, with its main() renamed.
# wireforge_selftest(<function> <example dir> <project> [DEFINES ...] [INCLUDES ...] [PLUGINS <file>])
set(generated ${CMAKE_BINARY_DIR}/generated)
set(selftest_sources "")
function(wireforge_selftest function example project)
  cmake_parse_arguments(PARSE_ARGV 3 arg "" "PLUGINS" "DEFINES;INCLUDES")
  set(plugins "")
  if(arg_PLUGINS)
    set(plugins --plugins ${arg_PLUGINS})
  endif()
  set(tests_dir ${generated}/${project}/generated_tests)
  file(GLOB_RECURSE model_files CONFIGURE_DEPENDS ${WIREFORGE_ROOT}/examples/${example}/*.yaml)
  add_custom_command(
    OUTPUT ${tests_dir}/selftest.cpp ${generated}/${project}/model.hpp
    COMMAND ${CMAKE_COMMAND} -E env PYTHONPATH=${WIREFORGE_ROOT}/codegen
            ${Python3_EXECUTABLE} -m wireforge.generate ${WIREFORGE_ROOT}/examples/${example}
            -o ${generated} --tests ${plugins}
    DEPENDS ${model_files} ${arg_PLUGINS}
    VERBATIM)
  set_source_files_properties(${tests_dir}/selftest.cpp PROPERTIES
    COMPILE_DEFINITIONS "main=${function};${arg_DEFINES}"
    INCLUDE_DIRECTORIES "${tests_dir};${arg_INCLUDES}")
  set(selftest_sources ${selftest_sources} ${tests_dir}/selftest.cpp PARENT_SCOPE)
endfunction()

wireforge_selftest(selftest_basic_telemetry 01_basic_telemetry basic_telemetry)
wireforge_selftest(selftest_avionics_platform_a 02_multi_interface_avionics avionics_demo
  DEFINES PLATFORM_A=1 MAX_TRACKS=3 CHANNELS=3 WIREFORGE_PROFILE_platformA
  INCLUDES ${WIREFORGE_ROOT}/examples/02_multi_interface_avionics/app/include
  PLUGINS ${WIREFORGE_ROOT}/examples/02_multi_interface_avionics/plugins/vendor_sum.py)
wireforge_selftest(selftest_edge_cases 03_edge_cases edge_cases)

add_executable(rtos_harness main.cpp startup.cpp ${selftest_sources})
target_include_directories(rtos_harness PRIVATE
  ${generated} ${WIREFORGE_ROOT}/runtime/include ${CMAKE_CURRENT_SOURCE_DIR})
# The lock of the runtime is the mutex of the kernel; no heap, exceptions or RTTI in the library.
target_compile_definitions(rtos_harness PRIVATE
  WIREFORGE_NO_STD_MUTEX WIREFORGE_DEFAULT_LOCK=FreeRtosLock)
target_compile_options(rtos_harness PRIVATE
  -fno-exceptions -fno-rtti -Wall -Wextra -Wconversion -Wno-psabi
  -include ${CMAKE_CURRENT_SOURCE_DIR}/freertos_lock.hpp)
target_link_options(rtos_harness PRIVATE -T ${CMAKE_CURRENT_SOURCE_DIR}/mps2_an386.ld
  -Wl,-Map=${CMAKE_BINARY_DIR}/rtos_harness.map)
target_link_libraries(rtos_harness PRIVATE freertos_kernel freertos_config)
