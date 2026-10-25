cmake_minimum_required(VERSION 3.25)
project(wireforge VERSION 0.4.1 LANGUAGES CXX C)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

option(WIREFORGE_BUILD_TESTS "Build the GoogleTest suites" ON)
option(WIREFORGE_BUILD_SELFTESTS "Build the framework-free self-tests of the generated examples" ON)
option(WIREFORGE_WEVERYTHING "Clang: -Weverything on runtime and generated code" OFF)
option(WIREFORGE_WERROR "Treat every warning as an error (policy: ON)" ON)
option(WIREFORGE_GCC_ANALYZER "Enable GCC -fanalyzer" OFF)
option(WIREFORGE_COVERAGE "Instrument for gcov/llvm-cov coverage" OFF)
option(WIREFORGE_FUZZ "Build the libFuzzer targets (Clang only)" OFF)
set(WIREFORGE_SANITIZE "" CACHE STRING "Sanitizers: address,undefined or thread")
option(WIREFORGE_UNSIGNED_CHAR "Compile with plain char unsigned, as on ARM" OFF)

include(cmake/WireforgeWarnings.cmake)

add_subdirectory(runtime)

# `cmake --install` puts the runtime headers and the CMake package (find_package(WireForge)) in a
# prefix; the Python package ships the same files (`wireforge cmake-dir`).
include(GNUInstallDirs)
install(DIRECTORY runtime/include/wireforge DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})
install(FILES cmake/package/WireForgeConfig.cmake cmake/package/WireForgeConfigVersion.cmake
              cmake/package/WireForgeGenerate.cmake
        DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/WireForge)

if(WIREFORGE_BUILD_TESTS OR WIREFORGE_BUILD_SELFTESTS)
  enable_testing()
  add_subdirectory(tests)
endif()
