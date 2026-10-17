#!/usr/bin/env bash
# Build with coverage instrumentation, run tests, print a gcovr summary for production code.
set -euo pipefail
cmake --preset coverage
cmake --build --preset coverage
ctest --preset coverage
mkdir -p build/coverage/report build/coverage/results
# GCC reports negative branch counts for some code (GCC bug 68080); gcovr treats them as errors
# unless told to warn once per file.
status=0
gcovr --root . --object-directory build/coverage \
  --filter 'runtime/include/' --filter 'codegen/' \
  --txt --html-details build/coverage/report/index.html \
  --json build/coverage/results/coverage.json \
  --gcov-ignore-parse-errors=negative_hits.warn_once_per_file \
  --fail-under-line "${WIREFORGE_COVERAGE_MIN_LINE:-0}" || status=$?
# The dossier of the runtime: requirements, tests, results and coverage (not gated here; the strict
# rules are reported in build/evidence-runtime/index.md).
PYTHONPATH=codegen python3 -m wireforge.cli docs --runtime -o build/evidence-runtime \
  --results build/coverage
exit "${status}"
