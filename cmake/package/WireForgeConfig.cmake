# CMake package of WireForge: find_package(WireForge) gives
#  - wireforge::runtime: the header-only C++17 runtime;
#  - wireforge_generate(): generates C++ code from a WireForge project at configure time.
# Works from an installed tree (<prefix>/lib/cmake/WireForge) and from the Python package
# (`wireforge cmake-dir` prints this directory).
include_guard(GLOBAL)

set(WIREFORGE_INCLUDE_DIR "")
foreach(candidate "${CMAKE_CURRENT_LIST_DIR}/../runtime/include" "${CMAKE_CURRENT_LIST_DIR}/../../../include"
        "${CMAKE_CURRENT_LIST_DIR}/../../runtime/include")
  if(NOT WIREFORGE_INCLUDE_DIR AND EXISTS "${candidate}/wireforge/status.hpp")
    get_filename_component(WIREFORGE_INCLUDE_DIR "${candidate}" ABSOLUTE)
  endif()
endforeach()
if(NOT WIREFORGE_INCLUDE_DIR)
  set(WireForge_FOUND FALSE)
  set(WireForge_NOT_FOUND_MESSAGE "the WireForge runtime headers were not found next to ${CMAKE_CURRENT_LIST_DIR}")
  return()
endif()

if(NOT TARGET wireforge::runtime)
  add_library(wireforge::runtime INTERFACE IMPORTED)
  set_target_properties(wireforge::runtime PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${WIREFORGE_INCLUDE_DIR}"
    INTERFACE_COMPILE_FEATURES cxx_std_17)
endif()

include("${CMAKE_CURRENT_LIST_DIR}/WireForgeGenerate.cmake")
set(WireForge_FOUND TRUE)
