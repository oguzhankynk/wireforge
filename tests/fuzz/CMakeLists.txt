# libFuzzer targets of the runtime (Clang only). Run them with quality/run_fuzz.sh.
if(NOT CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  message(FATAL_ERROR "WIREFORGE_FUZZ needs Clang (libFuzzer)")
endif()

function(wireforge_add_fuzzer name)
  add_executable(${name} ${ARGN})
  target_compile_options(${name} PRIVATE -fsanitize=fuzzer,address,undefined
                         -fno-sanitize-recover=all)
  target_link_options(${name} PRIVATE -fsanitize=fuzzer,address,undefined)
  target_link_libraries(${name} PRIVATE wireforge::runtime wireforge_warnings)
endfunction()

wireforge_add_fuzzer(checksum_fuzz checksum_fuzz.cpp)
wireforge_add_fuzzer(frame_decoder_fuzz frame_decoder_fuzz.cpp)
