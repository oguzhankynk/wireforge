if(WIREFORGE_BUILD_TESTS)
  find_package(GTest 1.14 QUIET)
  if(NOT GTest_FOUND)
    include(FetchContent)
    set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)
    set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
    FetchContent_Declare(googletest
      GIT_REPOSITORY https://github.com/google/googletest.git
      GIT_TAG v1.14.0
      GIT_SHALLOW TRUE
      SYSTEM)
    FetchContent_MakeAvailable(googletest)
  endif()

  include(GoogleTest)
  find_package(Threads REQUIRED)

  add_library(wireforge_test_support STATIC support/alloc_trap.cpp)
  target_include_directories(wireforge_test_support PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
  target_link_libraries(wireforge_test_support PRIVATE wireforge_warnings wireforge_instrumentation)

  function(wireforge_add_test name)
    add_executable(${name} ${ARGN})
    target_link_libraries(${name} PRIVATE wireforge::runtime wireforge_test_support
      GTest::gtest_main Threads::Threads wireforge_warnings wireforge_instrumentation)
    gtest_discover_tests(${name} DISCOVERY_MODE PRE_TEST
                         XML_OUTPUT_DIR ${CMAKE_BINARY_DIR}/results/junit/${name})
  endfunction()

  wireforge_add_test(smoke_test smoke_test.cpp)
  wireforge_add_test(io_test io_test.cpp)
  wireforge_add_test(containers_test containers_test.cpp)
  wireforge_add_test(ring_buffer_test ring_buffer_test.cpp)
  wireforge_add_test(checksum_test checksum_test.cpp)
  wireforge_add_test(frame_decoder_test frame_decoder_test.cpp)
  wireforge_add_test(dispatcher_test dispatcher_test.cpp)
  wireforge_add_test(link_test link_test.cpp)
  wireforge_add_test(validation_test validation_test.cpp)
  wireforge_add_test(codec_support_test codec_support_test.cpp)
  wireforge_add_test(lock_config_test lock_config_test.cpp)
  target_compile_definitions(lock_config_test PRIVATE WIREFORGE_NO_STD_MUTEX)
endif()

# The C runtime is tested by a plain C program (no framework): it must compile as C99 with the
# warning policy of C code and every check must hold.
add_executable(c_runtime_test c/runtime_test.c)
target_link_libraries(c_runtime_test PRIVATE wireforge::runtime_c wireforge_c_warnings
  wireforge_instrumentation)
if(NOT MSVC)
  target_link_libraries(c_runtime_test PRIVATE m)
endif()
add_test(NAME c_runtime_test COMMAND c_runtime_test)

add_subdirectory(generated)
if(WIREFORGE_FUZZ)
  add_subdirectory(fuzz)
endif()
