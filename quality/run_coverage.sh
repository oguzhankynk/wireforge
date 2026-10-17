#!/usr/bin/env bash
# Build with coverage instrumentation, run tests, print a gcovr summary for production code.
set -euo pipefail
cmake --preset coverage
cmake --build --preset coverage
ctest --preset coverage
mkdir -p build/coverage/report
# GCC reports negative branch counts for some code (GCC bug 68080); gcovr treats them as errors
# unless told to warn once per file.
gcovr --root . --object-directory build/coverage \
  --filter 'runtime/include/' --filter 'codegen/' \
  --txt --html-details build/coverage/report/index.html \
  --gcov-ignore-parse-errors=negative_hits.warn_once_per_file \
  --fail-under-line "${WIREFORGE_COVERAGE_MIN_LINE:-0}"
