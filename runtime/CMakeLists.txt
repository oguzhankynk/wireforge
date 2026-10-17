add_library(wireforge_runtime INTERFACE)
add_library(wireforge::runtime ALIAS wireforge_runtime)
target_include_directories(wireforge_runtime INTERFACE
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>)
target_compile_features(wireforge_runtime INTERFACE cxx_std_17)

#Every public header must compile on its own and is analysed as a translation unit.
file(GLOB_RECURSE WIREFORGE_PUBLIC_HEADERS CONFIGURE_DEPENDS
  RELATIVE ${CMAKE_CURRENT_SOURCE_DIR}/include
  ${CMAKE_CURRENT_SOURCE_DIR}/include/*.hpp)
set(WIREFORGE_HEADER_CHECK_SOURCES "")
foreach(header IN LISTS WIREFORGE_PUBLIC_HEADERS)
  string(MAKE_C_IDENTIFIER "${header}" header_id)
  set(check_source "${CMAKE_CURRENT_BINARY_DIR}/header_check/${header_id}.cpp")
  file(CONFIGURE OUTPUT "${check_source}" CONTENT "#include <${header}>\n" @ONLY)
  list(APPEND WIREFORGE_HEADER_CHECK_SOURCES "${check_source}")
endforeach()
add_library(wireforge_header_check OBJECT ${WIREFORGE_HEADER_CHECK_SOURCES})
target_link_libraries(wireforge_header_check PRIVATE wireforge::runtime wireforge_warnings
  wireforge_gcc_analyzer wireforge_instrumentation wireforge_no_exceptions wireforge_everything)
