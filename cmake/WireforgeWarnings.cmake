# Interface targets carrying the project-wide quality flags (see quality/QUALITY_POLICY.md).
add_library(wireforge_warnings INTERFACE)

if(MSVC)
  target_compile_options(wireforge_warnings INTERFACE /W4 /permissive- /Zc:__cplusplus)
  if(WIREFORGE_WERROR)
    target_compile_options(wireforge_warnings INTERFACE /WX)
  endif()
else()
  target_compile_options(wireforge_warnings INTERFACE
    -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Wold-style-cast
    -Wcast-align -Wcast-qual -Wnon-virtual-dtor -Woverloaded-virtual -Wnull-dereference
    -Wdouble-promotion -Wformat=2 -Wundef -Wunused -Wswitch-enum)
  if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    target_compile_options(wireforge_warnings INTERFACE
      -Wuseless-cast -Wduplicated-cond -Wduplicated-branches -Wlogical-op)
  else()
    target_compile_options(wireforge_warnings INTERFACE -Wextra-semi -Wimplicit-fallthrough)
  endif()
  if(WIREFORGE_WERROR)
    target_compile_options(wireforge_warnings INTERFACE -Werror)
  endif()
endif()

# The same policy for C99 code (the C runtime and the C code that wireforge generates).
add_library(wireforge_c_warnings INTERFACE)
if(MSVC)
  target_compile_options(wireforge_c_warnings INTERFACE /W4)
  if(WIREFORGE_WERROR)
    target_compile_options(wireforge_c_warnings INTERFACE /WX)
  endif()
else()
  target_compile_options(wireforge_c_warnings INTERFACE
    -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow -Wcast-qual -Wcast-align
    -Wnull-dereference -Wdouble-promotion -Wformat=2 -Wundef -Wunused -Wswitch-enum
    -Wstrict-prototypes -Wold-style-definition -Wmissing-prototypes)
  if(CMAKE_C_COMPILER_ID STREQUAL "GNU")
    target_compile_options(wireforge_c_warnings INTERFACE
      -Wduplicated-cond -Wduplicated-branches -Wlogical-op)
  endif()
  if(WIREFORGE_WEVERYTHING AND CMAKE_C_COMPILER_ID MATCHES "Clang")
    target_compile_options(wireforge_c_warnings INTERFACE -Weverything -Wno-padded
      -Wno-declaration-after-statement -Wno-unsafe-buffer-usage -Wno-unknown-warning-option)
  endif()
  if(WIREFORGE_WERROR)
    target_compile_options(wireforge_c_warnings INTERFACE -Werror)
  endif()
endif()

add_library(wireforge_instrumentation INTERFACE)
if(WIREFORGE_SANITIZE AND NOT MSVC)
  target_compile_options(wireforge_instrumentation INTERFACE
    -fsanitize=${WIREFORGE_SANITIZE} -fno-omit-frame-pointer -fno-sanitize-recover=all)
  target_link_options(wireforge_instrumentation INTERFACE -fsanitize=${WIREFORGE_SANITIZE})
endif()
if(WIREFORGE_UNSIGNED_CHAR AND NOT MSVC)
  target_compile_options(wireforge_instrumentation INTERFACE -funsigned-char)
endif()
if(WIREFORGE_COVERAGE AND NOT MSVC)
  target_compile_options(wireforge_instrumentation INTERFACE --coverage -O0 -g)
  target_link_options(wireforge_instrumentation INTERFACE --coverage)
endif()

# GCC 13's -fanalyzer reports false positives on libstdc++/gtest code in C++, so it is applied
# only to production code (runtime headers, generated code), never to test translation units.
add_library(wireforge_gcc_analyzer INTERFACE)
if(WIREFORGE_GCC_ANALYZER AND CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
  target_compile_options(wireforge_gcc_analyzer INTERFACE -fanalyzer)
endif()

# Production code is also compiled the way embedded users build it: no exceptions, no RTTI.
add_library(wireforge_no_exceptions INTERFACE)
if(MSVC)
  target_compile_options(wireforge_no_exceptions INTERFACE /GR-)
else()
  target_compile_options(wireforge_no_exceptions INTERFACE -fno-exceptions -fno-rtti)
endif()

# Clang -Weverything on production code; the exclusions are listed in quality/QUALITY_POLICY.md.
add_library(wireforge_everything INTERFACE)
if(WIREFORGE_WEVERYTHING AND CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  target_compile_options(wireforge_everything INTERFACE -Weverything -Wno-c++98-compat
    -Wno-c++98-compat-pedantic -Wno-padded -Wno-weak-vtables -Wno-unknown-warning-option)
endif()
