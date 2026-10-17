# Code generated from examples/ is compiled with the project warnings, analysed by every gate and
# exercised by hand-written tests. Requires the Python package (pip install -e .).
find_package(Python3 COMPONENTS Interpreter)
set(WIREFORGE_PYTHON_OK FALSE)
if(Python3_Interpreter_FOUND)
  execute_process(
    COMMAND ${CMAKE_COMMAND} -E env PYTHONPATH=${PROJECT_SOURCE_DIR}/codegen
            ${Python3_EXECUTABLE} -c "import ruamel.yaml, jsonschema, referencing"
    RESULT_VARIABLE wireforge_python_result OUTPUT_QUIET ERROR_QUIET)
  if(wireforge_python_result EQUAL 0)
    set(WIREFORGE_PYTHON_OK TRUE)
  endif()
endif()
if(NOT WIREFORGE_PYTHON_OK)
  message(STATUS "WireForge: Python dependencies missing, generated-code tests are skipped")
  return()
endif()

set(WIREFORGE_GENERATED_DIR ${CMAKE_CURRENT_BINARY_DIR}/include)
file(GLOB WIREFORGE_GENERATOR_SOURCES CONFIGURE_DEPENDS ${PROJECT_SOURCE_DIR}/codegen/wireforge/*.py)

# wireforge_generate(<target> <example dir> <project name> <extra outputs...> [PLUGINS <file>])
# Generates the C++ code and the GoogleTest project generated_tests/ of an example.
function(wireforge_generate target example project)
  cmake_parse_arguments(PARSE_ARGV 3 arg "" "PLUGINS" "")
  file(GLOB_RECURSE model_files CONFIGURE_DEPENDS ${PROJECT_SOURCE_DIR}/examples/${example}/*.yaml)
  set(tests_dir ${WIREFORGE_GENERATED_DIR}/${project}/generated_tests)
  set(outputs ${WIREFORGE_GENERATED_DIR}/${project}/model.hpp
              ${WIREFORGE_GENERATED_DIR}/${project}/user_prelude.hpp ${arg_UNPARSED_ARGUMENTS}
              ${tests_dir}/golden_test.cpp ${tests_dir}/message_test.cpp
              ${tests_dir}/interface_test.cpp ${tests_dir}/handlers.hpp
              ${tests_dir}/test_support.hpp ${tests_dir}/wire_support.hpp
              ${tests_dir}/diff_harness.cpp ${tests_dir}/fuzz_target.cpp ${tests_dir}/selftest.cpp
              ${tests_dir}/CMakeLists.txt)
  set(plugins "")
  if(arg_PLUGINS)
    set(plugins --plugins ${arg_PLUGINS})
  endif()
  add_custom_command(
    OUTPUT ${outputs}
    COMMAND ${CMAKE_COMMAND} -E env PYTHONPATH=${PROJECT_SOURCE_DIR}/codegen
            ${Python3_EXECUTABLE} -m wireforge.generate ${PROJECT_SOURCE_DIR}/examples/${example}
            -o ${WIREFORGE_GENERATED_DIR} --tests ${plugins}
    DEPENDS ${model_files} ${WIREFORGE_GENERATOR_SOURCES} ${arg_PLUGINS}
    COMMENT "Generating C++ and tests for ${example}"
    VERBATIM)
  add_custom_target(${target} DEPENDS ${outputs})
endfunction()

set(gen ${WIREFORGE_GENERATED_DIR})
wireforge_generate(generate_basic_telemetry 01_basic_telemetry basic_telemetry
                   ${gen}/basic_telemetry/interfaces/sensor_to_us.hpp)
wireforge_generate(generate_avionics_demo 02_multi_interface_avionics avionics_demo
                   ${gen}/avionics_demo/interfaces/nodeB_to_our.hpp
                   ${gen}/avionics_demo/interfaces/our_to_nodeA.hpp
                   ${gen}/avionics_demo/interfaces/our_to_nodeC.hpp
                   PLUGINS ${PROJECT_SOURCE_DIR}/examples/02_multi_interface_avionics/plugins/vendor_sum.py)
wireforge_generate(generate_edge_cases 03_edge_cases edge_cases
                   ${gen}/edge_cases/interfaces/loopback.hpp)

set(WIREFORGE_AVIONICS_INCLUDE ${PROJECT_SOURCE_DIR}/examples/02_multi_interface_avionics/app/include)
set(WIREFORGE_PROFILE_A PLATFORM_A=1 MAX_TRACKS=3 CHANNELS=3 WIREFORGE_PROFILE_platformA)
set(WIREFORGE_PROFILE_B PLATFORM_B=1 MAX_TRACKS=5 CHANNELS=5 WIREFORGE_PROFILE_platformB)

# Every generated header as its own translation unit, analysed like runtime code, per profile.
set(check_dir ${CMAKE_CURRENT_BINARY_DIR}/header_check)
set(check_sources "")
foreach(header basic_telemetry/model.hpp basic_telemetry/interfaces/sensor_to_us.hpp
        avionics_demo/model.hpp avionics_demo/interfaces/nodeB_to_our.hpp
        avionics_demo/interfaces/our_to_nodeA.hpp avionics_demo/interfaces/our_to_nodeC.hpp
        edge_cases/model.hpp edge_cases/interfaces/loopback.hpp)
  string(MAKE_C_IDENTIFIER "${header}" header_id)
  file(CONFIGURE OUTPUT ${check_dir}/${header_id}.cpp CONTENT "#include \"${header}\"\n")
  list(APPEND check_sources ${check_dir}/${header_id}.cpp)
endforeach()
foreach(profile A B)
  set(check wireforge_generated_check_${profile})
  add_library(${check} OBJECT ${check_sources})
  add_dependencies(${check} generate_basic_telemetry generate_avionics_demo generate_edge_cases)
  target_include_directories(${check} PRIVATE ${WIREFORGE_GENERATED_DIR}
                             ${WIREFORGE_AVIONICS_INCLUDE})
  target_compile_definitions(${check} PRIVATE ${WIREFORGE_PROFILE_${profile}})
  target_link_libraries(${check} PRIVATE wireforge::runtime wireforge_warnings
                        wireforge_gcc_analyzer wireforge_instrumentation wireforge_no_exceptions
                        wireforge_everything)
endforeach()

# Framework-free self-tests (golden vectors, decode cases): built without exceptions and RTTI,
# they also run on cross targets (big-endian, 32-bit) under an emulator.
if(WIREFORGE_BUILD_SELFTESTS)
  # wireforge_selftest(<name> <project> <generate target> <definitions...>)
  function(wireforge_selftest name project generator)
    set(tests_dir ${WIREFORGE_GENERATED_DIR}/${project}/generated_tests)
    add_executable(${name} ${tests_dir}/selftest.cpp)
    add_dependencies(${name} ${generator})
    target_include_directories(${name} PRIVATE ${tests_dir} ${WIREFORGE_GENERATED_DIR}
                               ${WIREFORGE_AVIONICS_INCLUDE})
    target_compile_definitions(${name} PRIVATE ${ARGN})
    target_link_libraries(${name} PRIVATE wireforge::runtime wireforge_warnings
                          wireforge_instrumentation wireforge_no_exceptions wireforge_everything)
    add_test(NAME ${name} COMMAND ${name})
  endfunction()
  wireforge_selftest(selftest_basic_telemetry basic_telemetry generate_basic_telemetry)
  wireforge_selftest(selftest_edge_cases edge_cases generate_edge_cases)
  wireforge_selftest(selftest_avionics_a avionics_demo generate_avionics_demo ${WIREFORGE_PROFILE_A})
  wireforge_selftest(selftest_avionics_b avionics_demo generate_avionics_demo ${WIREFORGE_PROFILE_B})
endif()

if(NOT WIREFORGE_BUILD_TESTS)
  return()
endif()

# wireforge_generated_test(<name> <source> <generate target> <definitions...>)
function(wireforge_generated_test name source generator)
  wireforge_add_test(${name} ${source})
  add_dependencies(${name} ${generator})
  target_include_directories(${name} PRIVATE ${WIREFORGE_GENERATED_DIR} ${WIREFORGE_AVIONICS_INCLUDE})
  target_compile_definitions(${name} PRIVATE ${ARGN})
endfunction()

wireforge_generated_test(generated_basic_telemetry_test basic_telemetry_test.cpp
                         generate_basic_telemetry)
wireforge_generated_test(generated_edge_cases_test edge_cases_test.cpp generate_edge_cases)
wireforge_generated_test(generated_avionics_a_test avionics_test.cpp generate_avionics_demo
                         ${WIREFORGE_PROFILE_A} EXPECTED_TRACKS=3)
wireforge_generated_test(generated_avionics_b_test avionics_test.cpp generate_avionics_demo
                         ${WIREFORGE_PROFILE_B} EXPECTED_TRACKS=5)

# The generated GoogleTest project of every example, built with the repository's quality gates.
# The standalone build of these projects (with their coverage target) is
# quality/run_generated_tests.sh.
# wireforge_generated_suite(<name> <project> <generate target> <definitions...>)
function(wireforge_generated_suite name project generator)
  set(tests_dir ${WIREFORGE_GENERATED_DIR}/${project}/generated_tests)
  wireforge_add_test(${name} ${tests_dir}/golden_test.cpp ${tests_dir}/message_test.cpp
                     ${tests_dir}/interface_test.cpp)
  add_dependencies(${name} ${generator})
  target_include_directories(${name} PRIVATE ${tests_dir} ${WIREFORGE_GENERATED_DIR}
                             ${WIREFORGE_AVIONICS_INCLUDE})
  target_compile_definitions(${name} PRIVATE ${ARGN})
  # Differential harness and fuzz target: compiled and analysed here; the harness is run by
  # quality/run_generated_tests.sh and quality/run_fuzz.sh.
  add_executable(${name}_diff ${tests_dir}/diff_harness.cpp)
  add_library(${name}_fuzz OBJECT ${tests_dir}/fuzz_target.cpp)
  # The fuzz target also runs here with deterministic inputs.
  wireforge_add_test(${name}_replay ${PROJECT_SOURCE_DIR}/tests/support/fuzz_replay.cpp
                     $<TARGET_OBJECTS:${name}_fuzz>)
  foreach(target ${name}_diff ${name}_fuzz ${name}_replay)
    add_dependencies(${target} ${generator})
    target_include_directories(${target} PRIVATE ${tests_dir} ${WIREFORGE_GENERATED_DIR}
                               ${WIREFORGE_AVIONICS_INCLUDE})
    target_compile_definitions(${target} PRIVATE ${ARGN})
    target_link_libraries(${target} PRIVATE wireforge::runtime wireforge_warnings
                          wireforge_instrumentation)
  endforeach()
endfunction()

wireforge_generated_suite(generated_suite_basic_telemetry basic_telemetry generate_basic_telemetry)
wireforge_generated_suite(generated_suite_edge_cases edge_cases generate_edge_cases)
wireforge_generated_suite(generated_suite_avionics_a avionics_demo generate_avionics_demo
                          ${WIREFORGE_PROFILE_A})
wireforge_generated_suite(generated_suite_avionics_b avionics_demo generate_avionics_demo
                          ${WIREFORGE_PROFILE_B})
